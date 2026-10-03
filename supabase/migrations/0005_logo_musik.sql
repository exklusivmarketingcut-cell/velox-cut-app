-- Velox Cut – Logo & Musik (30.09.2026)
-- Kunden können ein Logo hinterlegen (R2: logos/<user>/…) und pro Auftrag wählen, ob es ins Video kommt
-- (Schutz vor Kopieren) und ob lizenzfreie Hintergrundmusik eingebaut wird. Trend-Song-Vorschläge stehen je Clip
-- in jobs.outputs[].songs (kein neues Feld nötig).
alter table public.profiles add column if not exists logo_path text;
alter table public.jobs add column if not exists use_logo boolean not null default false;
alter table public.jobs add column if not exists music text not null default 'none';
alter table public.jobs drop constraint if exists jobs_music_check;
alter table public.jobs add constraint jobs_music_check check (music in ('none', 'auto'));
