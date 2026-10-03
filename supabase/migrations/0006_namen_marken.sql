-- Velox Cut – Namen & Marken (01.10.2026)
-- Der Kunde trägt einmal ein, welche Namen/Marken in seinen Videos vorkommen (z. B. „Malo's, Corndogs, Smashburger“).
-- Spracherkennung, Lektorat und KI-Abnahme schreiben sie dann richtig (häufigster Untertitel-Fehler bei Restaurants).
alter table public.profiles add column if not exists brand_words text;
