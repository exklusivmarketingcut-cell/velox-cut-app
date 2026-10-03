-- Velox Cut – Wächter schneller: prüft alle 30 Sekunden, greift nach 5 Min. (laufender Auftrag ohne Lebenszeichen)
-- bzw. 10 Min. (wartender Auftrag, keine Schnitt-Maschine erreichbar) ein. Enthält auch die Umlaut-Korrektur.
create or replace function public.watchdog()
returns integer language plpgsql security definer set search_path = public as $$
declare
  j record;
  n integer := 0;
  alive boolean := exists (select 1 from public.workers where last_seen > now() - interval '3 minutes');
begin
  for j in
    select jb.id, jb.status from public.jobs jb
    where
      -- in Arbeit, aber die Maschine arbeitet nicht (mehr) daran: abgestürzt, ausgeschaltet, offline
      (jb.status = 'processing' and jb.started_at < now() - interval '5 minutes' and not exists (
         select 1 from public.workers w
         where w.name = jb.worker and w.busy_job = jb.id and w.last_seen > now() - interval '5 minutes'))
      -- in Arbeit, aber seit 40 Minuten keine Rückmeldung der Pipeline (hängt) oder insgesamt über 5 Stunden
      or (jb.status = 'processing' and (jb.started_at < now() - interval '5 hours' or exists (
         select 1 from public.workers w
         where w.name = jb.worker and w.busy_job = jb.id and w.last_output < now() - interval '40 minutes')))
      -- wartet, aber keine Maschine erreichbar – nach 10 Minuten Geld zurück statt ewig warten
      or (jb.status = 'queued' and not alive and jb.created_at < now() - interval '10 minutes')
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
select cron.schedule('velox-watchdog', '30 seconds', 'select public.watchdog()');
