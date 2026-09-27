-- Was die Schnitt-Maschine gerade macht (für die Fortschrittsanzeige im Studio)
alter table public.jobs add column if not exists stage text;
