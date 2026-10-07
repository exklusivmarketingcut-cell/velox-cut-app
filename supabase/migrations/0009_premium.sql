-- 0009: Stil „Premium“ für Aufträge zulassen (bisher nur clean/viral).
-- Einmal im Supabase-SQL-Editor ausführen. Danach in src/lib/pricing.ts PREMIUM_AKTIV auf true stellen und neu veröffentlichen.
alter table public.jobs drop constraint if exists jobs_style_check;
alter table public.jobs add constraint jobs_style_check check (style in ('clean', 'viral', 'premium'));
