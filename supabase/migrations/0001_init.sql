-- Velox Cut Studio – Grundschema
-- Im Supabase-Dashboard unter "SQL Editor" einmal komplett ausführen.

-- ---------- Profile ----------
create table if not exists public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  email text not null,
  name text,
  stripe_customer_id text unique,
  default_payment_method text,
  card_on_file boolean not null default false,
  plan text not null default 'free' check (plan in ('free', 'starter', 'pro', 'business')),
  plan_interval text check (plan_interval in ('monthly', 'yearly')),
  plan_status text,                         -- Stripe-Status: active, past_due, canceled …
  subscription_id text,
  plan_credits integer not null default 10 check (plan_credits >= 0),   -- verfallen pro Abrechnungsmonat
  topup_credits integer not null default 0 check (topup_credits >= 0),  -- Nachkauf, verfällt nie
  free_refill_at timestamptz not null default now() + interval '30 days',
  plan_credits_reset_at timestamptz,         -- letzter Monats-Reset (für Jahres-Abos)
  created_at timestamptz not null default now()
);

-- ---------- Credit-Buchungen (Nachweis jeder Änderung) ----------
create table if not exists public.credit_ledger (
  id bigserial primary key,
  user_id uuid not null references public.profiles (id) on delete cascade,
  delta integer not null,
  bucket text not null check (bucket in ('plan', 'topup')),
  reason text not null,          -- signup, free_refill, plan_period, topup, job, refund …
  ref text,                      -- Stripe-ID oder Job-ID
  created_at timestamptz not null default now()
);
create index if not exists credit_ledger_user_idx on public.credit_ledger (user_id, created_at desc);
-- Jede Stripe-Zahlung darf höchstens einmal Credits gutschreiben
create unique index if not exists credit_ledger_ref_once on public.credit_ledger (ref, reason) where reason in ('topup', 'plan_period');

-- ---------- Aufträge ----------
create table if not exists public.jobs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles (id) on delete cascade,
  status text not null default 'queued' check (status in ('queued', 'processing', 'done', 'failed')),
  style text not null check (style in ('clean', 'viral')),
  caption_style text not null default 'rise',
  caption_color text not null default '#F6E27F',
  clips integer not null default 1 check (clips between 1 and 5),
  source_kind text not null check (source_kind in ('upload', 'drive')),
  source_path text,              -- Pfad im Bucket "uploads"
  source_url text,               -- Google-Drive-Link
  source_name text,
  credits_charged integer not null,
  progress integer not null default 0,
  outputs jsonb not null default '[]'::jsonb,  -- [{path, title, seconds}]
  error text,
  worker text,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz
);
create index if not exists jobs_user_idx on public.jobs (user_id, created_at desc);
create index if not exists jobs_queue_idx on public.jobs (created_at) where status = 'queued';

-- ---------- Formulare der Website ----------
create table if not exists public.beta_requests (
  id bigserial primary key,
  topic text, name text not null, email text not null, message text,
  created_at timestamptz not null default now()
);
create table if not exists public.cancellations (
  id bigserial primary key,
  art text not null, grund text, name text not null, email text not null,
  paket text not null, vertragsnummer text, zeitpunkt text not null, datum text,
  abgegeben text,                -- Zeitpunkt laut Formular (Anzeige für den Kunden)
  created_at timestamptz not null default now()
);

-- ---------- Stripe-Webhooks nur einmal verarbeiten ----------
create table if not exists public.stripe_events (
  id text primary key,
  type text not null,
  created_at timestamptz not null default now()
);

-- ---------- Zugriffsschutz: Nutzer sehen nur ihre eigenen Daten, schreiben nur über den Server ----------
alter table public.profiles enable row level security;
alter table public.credit_ledger enable row level security;
alter table public.jobs enable row level security;
alter table public.beta_requests enable row level security;
alter table public.cancellations enable row level security;
alter table public.stripe_events enable row level security;

drop policy if exists "eigenes Profil lesen" on public.profiles;
create policy "eigenes Profil lesen" on public.profiles for select using (auth.uid() = id);
drop policy if exists "eigene Buchungen lesen" on public.credit_ledger;
create policy "eigene Buchungen lesen" on public.credit_ledger for select using (auth.uid() = user_id);
drop policy if exists "eigene Aufträge lesen" on public.jobs;
create policy "eigene Aufträge lesen" on public.jobs for select using (auth.uid() = user_id);

-- ---------- Neues Konto → Profil mit 10 Gratis-Credits ----------
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, email, name)
  values (new.id, new.email, new.raw_user_meta_data ->> 'name')
  on conflict (id) do nothing;
  insert into public.credit_ledger (user_id, delta, bucket, reason) values (new.id, 10, 'plan', 'signup');
  return new;
end $$;
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---------- Credits abbuchen: atomar, Paket-Credits zuerst, dann Nachkauf ----------
create or replace function public.spend_credits(p_user uuid, p_amount integer, p_reason text, p_ref text)
returns boolean language plpgsql security definer set search_path = public as $$
declare p public.profiles; from_plan integer; from_topup integer;
begin
  select * into p from public.profiles where id = p_user for update;
  if not found then return false; end if;
  -- Free: monatlich wieder auf 10 auffüllen
  if p.plan = 'free' and p.free_refill_at <= now() then
    insert into public.credit_ledger (user_id, delta, bucket, reason)
      values (p_user, 10 - p.plan_credits, 'plan', 'free_refill');
    update public.profiles set plan_credits = 10, free_refill_at = now() + interval '30 days' where id = p_user;
    p.plan_credits := 10;
  end if;
  if p.plan_credits + p.topup_credits < p_amount then return false; end if;
  from_plan := least(p.plan_credits, p_amount);
  from_topup := p_amount - from_plan;
  update public.profiles set plan_credits = plan_credits - from_plan, topup_credits = topup_credits - from_topup where id = p_user;
  if from_plan > 0 then insert into public.credit_ledger (user_id, delta, bucket, reason, ref) values (p_user, -from_plan, 'plan', p_reason, p_ref); end if;
  if from_topup > 0 then insert into public.credit_ledger (user_id, delta, bucket, reason, ref) values (p_user, -from_topup, 'topup', p_reason, p_ref); end if;
  return true;
end $$;

-- ---------- Nachkauf gutschreiben (idempotent über ref) ----------
create or replace function public.grant_topup(p_user uuid, p_amount integer, p_ref text)
returns boolean language plpgsql security definer set search_path = public as $$
begin
  insert into public.credit_ledger (user_id, delta, bucket, reason, ref) values (p_user, p_amount, 'topup', 'topup', p_ref);
  update public.profiles set topup_credits = topup_credits + p_amount where id = p_user;
  return true;
exception when unique_violation then
  return false; -- schon gutgeschrieben
end $$;

-- ---------- Neuer Abrechnungsmonat: Paket-Credits auf die Paketmenge setzen (idempotent über ref) ----------
create or replace function public.reset_plan_credits(p_user uuid, p_amount integer, p_ref text)
returns boolean language plpgsql security definer set search_path = public as $$
declare cur integer;
begin
  select plan_credits into cur from public.profiles where id = p_user for update;
  insert into public.credit_ledger (user_id, delta, bucket, reason, ref) values (p_user, p_amount - cur, 'plan', 'plan_period', p_ref);
  update public.profiles set plan_credits = p_amount, plan_credits_reset_at = now() where id = p_user;
  return true;
exception when unique_violation then
  return false;
end $$;

-- ---------- Beim Öffnen der App: fällige Monats-Credits nachtragen ----------
-- Free: alle 30 Tage wieder 10. Jahres-Abo: alle 30 Tage wieder die Paketmenge (Monats-Abos macht der Stripe-Webhook).
create or replace function public.refresh_credits(p_user uuid, p_plan_amount integer)
returns void language plpgsql security definer set search_path = public as $$
declare p public.profiles;
begin
  select * into p from public.profiles where id = p_user for update;
  if not found then return; end if;
  if p.plan = 'free' and p.free_refill_at <= now() then
    insert into public.credit_ledger (user_id, delta, bucket, reason) values (p_user, 10 - p.plan_credits, 'plan', 'free_refill');
    update public.profiles set plan_credits = 10, free_refill_at = now() + interval '30 days' where id = p_user;
  elsif p.plan_interval = 'yearly' and p.plan_status in ('active', 'trialing')
        and p.plan_credits_reset_at is not null and p.plan_credits_reset_at <= now() - interval '30 days' then
    insert into public.credit_ledger (user_id, delta, bucket, reason) values (p_user, p_plan_amount - p.plan_credits, 'plan', 'yearly_month');
    update public.profiles set plan_credits = p_plan_amount, plan_credits_reset_at = now() where id = p_user;
  end if;
end $$;

-- ---------- Abo beendet → zurück auf Free ----------
create or replace function public.downgrade_to_free(p_user uuid)
returns void language plpgsql security definer set search_path = public as $$
declare cur integer;
begin
  select plan_credits into cur from public.profiles where id = p_user for update;
  insert into public.credit_ledger (user_id, delta, bucket, reason) values (p_user, 10 - cur, 'plan', 'downgrade');
  update public.profiles set plan = 'free', plan_interval = null, plan_status = 'canceled', subscription_id = null,
    plan_credits = 10, free_refill_at = now() + interval '30 days' where id = p_user;
end $$;

-- ---------- Fehlgeschlagener Auftrag: Credits zurück in den Nachkauf-Topf (verfallen dann nicht) ----------
create or replace function public.refund_job(p_job uuid)
returns boolean language plpgsql security definer set search_path = public as $$
declare j public.jobs;
begin
  select * into j from public.jobs where id = p_job for update;
  if not found or j.credits_charged <= 0 then return false; end if;
  insert into public.credit_ledger (user_id, delta, bucket, reason, ref) values (j.user_id, j.credits_charged, 'topup', 'refund', p_job::text);
  update public.profiles set topup_credits = topup_credits + j.credits_charged where id = j.user_id;
  update public.jobs set credits_charged = 0 where id = p_job;
  return true;
end $$;

-- ---------- Credits erstatten (z. B. Auftrag ließ sich nicht anlegen) – atomar ----------
create or replace function public.refund_credits(p_user uuid, p_amount integer, p_ref text)
returns boolean language plpgsql security definer set search_path = public as $$
begin
  insert into public.credit_ledger (user_id, delta, bucket, reason, ref) values (p_user, p_amount, 'topup', 'refund', p_ref);
  update public.profiles set topup_credits = topup_credits + p_amount where id = p_user;
  return true;
end $$;

-- ---------- Schnitt-Maschine holt sich den nächsten Auftrag (sicher bei mehreren Maschinen) ----------
create or replace function public.claim_next_job(p_worker text)
returns setof public.jobs language plpgsql security definer set search_path = public as $$
begin
  return query
  update public.jobs set status = 'processing', worker = p_worker, started_at = now(), progress = 1
  where id = (select id from public.jobs where status = 'queued' order by created_at for update skip locked limit 1)
  returning *;
end $$;

-- Nur der Server (service_role) darf diese Funktionen aufrufen
revoke execute on function public.spend_credits(uuid, integer, text, text) from public, anon, authenticated;
revoke execute on function public.grant_topup(uuid, integer, text) from public, anon, authenticated;
revoke execute on function public.reset_plan_credits(uuid, integer, text) from public, anon, authenticated;
revoke execute on function public.refund_job(uuid) from public, anon, authenticated;
revoke execute on function public.refund_credits(uuid, integer, text) from public, anon, authenticated;
revoke execute on function public.refresh_credits(uuid, integer) from public, anon, authenticated;
revoke execute on function public.downgrade_to_free(uuid) from public, anon, authenticated;
revoke execute on function public.claim_next_job(text) from public, anon, authenticated;

-- ---------- Speicher: private Buckets, Zugriff nur über signierte Links vom Server ----------
insert into storage.buckets (id, name, public) values ('uploads', 'uploads', false) on conflict (id) do nothing;
insert into storage.buckets (id, name, public) values ('outputs', 'outputs', false) on conflict (id) do nothing;
