-- Velox Cut – mehrere Clips / ganzer Ordner zu einem Video (02.10.2026)
-- source_kind 'folder': alle Dateien (Videos + Bilder) liegen in R2 unter uploads/<user>/<stapel>/…, die Liste steht in source_paths.
alter table public.jobs add column if not exists source_paths jsonb not null default '[]'::jsonb;
alter table public.jobs drop constraint if exists jobs_source_kind_check;
alter table public.jobs add constraint jobs_source_kind_check check (source_kind in ('upload', 'drive', 'folder'));
