-- Velox Cut – Wächter: kein Kunde bezahlt Credits, ohne ein Video oder sein Geld zurückzubekommen.
-- Die Schnitt-Maschine meldet sich jede Minute („lebt noch“). Ist sie weg oder hängt ein Auftrag,
-- wird der Auftrag automatisch beendet und die Credits werden erstattet (alle 5 Minuten geprüft).

-- ---------- Lebenszeichen der Schnitt-Maschinen (nur Service-Rolle, keine Kundendaten) ----------
create table if not exists public.workers (
  name text primary key,
  last_seen timestamptz not null default now(),
  busy_job uuid,                 -- Auftrag, an dem gerade gearbeitet wird
  last_output timestamptz,       -- letzte Rückmeldung der Schnitt-Pipeline (hängt sie?)
  started_at timestamptz         -- seit wann die Maschine läuft
);
alter table public.workers enable row level security;  -- keine Policies → nur Service-Rolle

-- ---------- Fürs Studio: ist gerade eine Schnitt-Maschine erreichbar? (nur ja/nein) ----------
create or replace function public.worker_online()
returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.workers where last_seen > now() - interval '3 minutes');
$$;
revoke execute on function public.worker_online() from public, anon;
grant execute on function public.worker_online() to authenticated, service_role;

-- ---------- Wächter ----------
create or replace function public.watchdog()
returns integer language plpgsql security definer set search_path = public as $$
declare
  j record;
  n integer := 0;
  alive boolean := exists (select 1 from public.workers where last_seen > now() - interval '10 minutes');
begin
  for j in
    select jb.id, jb.status from public.jobs jb
    where
      -- in Arbeit, aber die Maschine arbeitet nicht (mehr) daran: abgestürzt, ausgeschaltet, offline
      (jb.status = 'processing' and jb.started_at < now() - interval '10 minutes' and not exists (
         select 1 from public.workers w
         where w.name = jb.worker and w.busy_job = jb.id and w.last_seen > now() - interval '10 minutes'))
      -- in Arbeit, aber seit 40 Minuten keine Rückmeldung der Pipeline (hängt) oder insgesamt über 5 Stunden
      or (jb.status = 'processing' and (jb.started_at < now() - interval '5 hours' or exists (
         select 1 from public.workers w
         where w.name = jb.worker and w.busy_job = jb.id and w.last_output < now() - interval '40 minutes')))
      -- wartet, aber keine Maschine erreichbar – nach 20 Minuten Geld zurück statt ewig warten
      or (jb.status = 'queued' and not alive and jb.created_at < now() - interval '20 minutes')
    for update of jb skip locked
  loop
    update public.jobs set
      status = 'failed',
      finished_at = now(),
      error = case when j.status = 'queued'
        then 'Die Schnitt-Maschine war gerade nicht erreichbar. Bitte starte den Auftrag später noch einmal'
        else 'Der Schnitt wurde unterbrochen. Bitte starte den Auftrag noch einmal' end
    where id = j.id;
    perform public.refund_job(j.id);
    n := n + 1;
  end loop;
  return n;
end $$;
revoke execute on function public.watchdog() from public, anon, authenticated;

-- alle 5 Minuten prüfen (pg_cron läuft in der Datenbank selbst – unabhängig von Mac und Webseite)
create extension if not exists pg_cron with schema pg_catalog;
grant usage on schema cron to postgres;
select cron.schedule('velox-watchdog', '*/5 * * * *', 'select public.watchdog()');
