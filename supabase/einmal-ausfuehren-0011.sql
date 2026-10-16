-- 0011: Zwischenstatus „review“ – Videos eines Lernkontos warten auf die Prüfung, bevor der Kunde sie sieht.
-- Einmal im Supabase-SQL-Editor ausführen. Der Wächter fasst nur „queued“ und „processing“ an – wartende Videos bleiben unberührt.
alter table public.jobs drop constraint if exists jobs_status_check;
alter table public.jobs add constraint jobs_status_check check (status in ('queued', 'processing', 'review', 'done', 'failed'));
