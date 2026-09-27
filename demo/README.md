# Demo-Durchlauf (lokal, ohne echte Konten)

1. `node demo/backend.mjs` – bildet Supabase (Port 54321) und Stripe-Testmodus (Port 12111) nach, Daten nur im Arbeitsspeicher.
2. In `.env.development.local` stehen die Demo-Werte (nur `npm run dev` lädt sie, nie der Build) (`NEXT_PUBLIC_DEMO=1` …) – **nie** bei Cloudflare eintragen.
3. App starten: `npm run dev -- -p 3010`
4. Schnitt-Maschine: `SUPABASE_URL=http://127.0.0.1:54321 SUPABASE_SERVICE_ROLE_KEY=demo-service node worker/worker.mjs`

Test-Kunde für die Vorführung (nur im Demo-Speicher, verschwindet beim Neustart):
- Vorname: Lena
- E-Mail: lena@kunde.test
- Passwort: Demo-f2731b6b

Stripe-Testkarten: 4242 4242 4242 4242 (klappt), 4000 0000 0000 0002 (abgelehnt), beliebiges Datum in der Zukunft, beliebige CVC.
