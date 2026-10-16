-- 0010: Test-Code für einen Kollegen – 5000 Credits, insgesamt nur EINMAL einlösbar (max_uses = 1), gültig bis 31.12.2026.
-- Einmal im Supabase-SQL-Editor ausführen. Die Schnitt-Maschine hebt Aufträge des Kontos, das diesen Code einlöst,
-- als Lernmaterial auf (schnitt-maschine/lern/) – alle anderen Kunden werden wie bisher sofort gelöscht.
insert into public.codes (code, art, credits, max_uses, valid_until, note)
values ('TEST-F7LF3Y4H', 'credits', 5000, 1, '2026-12-31 23:59:59+01', 'Testkonto Kollege – Videos dienen als Lernmaterial')
on conflict (code) do nothing;
