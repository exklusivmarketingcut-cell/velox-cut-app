-- 0008: Gutschein-Codes, die der Betreiber in /admin anlegt.
--   credits       → sofort X Credits (verfallen nie), ohne Abo
--   gratis_monat  → Paket (z. B. Starter) 1 Monat für 0 € (Stripe-Testzeitraum), danach regulär, monatlich kündbar
-- Einlösen nur über den Server (service_role). Jedes Konto und jede Karte kann einen Code nur einmal nutzen.

create table if not exists public.codes (
  code text primary key check (code = upper(code) and code ~ '^[A-Z0-9_-]{3,30}$'),
  art text not null check (art in ('credits', 'gratis_monat')),
  credits integer not null default 0 check (credits >= 0 and credits <= 5000),
  plan text check (plan in ('starter', 'pro', 'business')),
  max_uses integer check (max_uses is null or max_uses > 0),   -- null = unbegrenzt
  uses integer not null default 0,
  valid_until timestamptz,                                       -- null = unbegrenzt
  active boolean not null default true,
  note text,
  created_at timestamptz not null default now(),
  check (art <> 'credits' or credits > 0),
  check (art <> 'gratis_monat' or plan is not null)
);
alter table public.codes enable row level security;

create table if not exists public.code_uses (
  code text not null references public.codes (code) on delete cascade,
  user_id uuid not null references public.profiles (id) on delete cascade,
  card_fp text,                                                  -- Karten-Fingerabdruck (Stripe), gegen Mehrfach-Konten
  created_at timestamptz not null default now(),
  primary key (code, user_id)
);
create unique index if not exists code_uses_card_once on public.code_uses (code, card_fp) where card_fp is not null;
alter table public.code_uses enable row level security;
drop policy if exists code_uses_own on public.code_uses;
create policy code_uses_own on public.code_uses for select using (auth.uid() = user_id);  -- eigene Einlösungen lesen

-- Prüfen und einlösen in einem Schritt (atomar). Ergebnis: ok | unbekannt | inaktiv | abgelaufen | aufgebraucht |
-- schon_eingeloest | karte_schon_genutzt | falsche_art
create or replace function public.redeem_code(p_code text, p_user uuid, p_card text, p_art text)
returns text language plpgsql security definer set search_path = public as $$
declare c public.codes;
begin
  select * into c from public.codes where code = upper(trim(p_code)) for update;
  if not found then return 'unbekannt'; end if;
  if not c.active then return 'inaktiv'; end if;
  if c.valid_until is not null and c.valid_until < now() then return 'abgelaufen'; end if;
  if c.max_uses is not null and c.uses >= c.max_uses then return 'aufgebraucht'; end if;
  if c.art <> p_art then return 'falsche_art'; end if;
  begin
    insert into public.code_uses (code, user_id, card_fp) values (c.code, p_user, p_card);
  exception when unique_violation then
    if exists (select 1 from public.code_uses where code = c.code and user_id = p_user) then return 'schon_eingeloest'; end if;
    return 'karte_schon_genutzt';
  end;
  update public.codes set uses = uses + 1 where code = c.code;
  if c.art = 'credits' then
    insert into public.credit_ledger (user_id, delta, bucket, reason, ref) values (p_user, c.credits, 'topup', 'topup', 'code-' || c.code || '-' || p_user);
    update public.profiles set topup_credits = topup_credits + c.credits where id = p_user;
  end if;
  return 'ok';
end $$;
revoke execute on function public.redeem_code(text, uuid, text, text) from public, anon, authenticated;

-- Einlösung zurücknehmen (z. B. wenn das Gratis-Abo bei Stripe nicht angelegt werden konnte)
create or replace function public.unredeem_code(p_code text, p_user uuid)
returns void language plpgsql security definer set search_path = public as $$
begin
  delete from public.code_uses where code = upper(trim(p_code)) and user_id = p_user;
  if found then update public.codes set uses = greatest(0, uses - 1) where code = upper(trim(p_code)); end if;
end $$;
revoke execute on function public.unredeem_code(text, uuid) from public, anon, authenticated;
