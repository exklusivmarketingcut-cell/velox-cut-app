-- 0012: Änderungswunsch (Revision) zu einem fertigen Video.
--   revision_note  = was der Kunde geändert haben möchte (letzter Wunsch)
--   revisions      = wie viele Änderungen es schon gab (1. gratis, danach ¼ Stil-Preis)
--   revision_cost  = Credits der laufenden Änderung (nur diese werden erstattet, wenn die Änderung scheitert)
--   keep_until     = so lange liegt das Rohmaterial noch bereit (24 Stunden nach dem Schnitt)
-- Einmal im Supabase-SQL-Editor ausführen.
alter table public.jobs add column if not exists revision_note text;
alter table public.jobs add column if not exists revisions integer not null default 0;
alter table public.jobs add column if not exists revision_cost integer not null default 0;
alter table public.jobs add column if not exists keep_until timestamptz;
