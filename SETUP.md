# Velox Cut Studio – Einrichtung & Launch

Dieses Projekt enthält **Website + App** in einem: Next.js auf **Vercel**, Login/Datenbank/Speicher über **Supabase**,
Zahlungen über **Stripe**, E-Mails über **Resend**. Die **Schnitt-Maschine** (`worker/`) läuft auf einem eigenen Rechner
(in der Beta: dein Mac), weil Vercel keine langen Video-Jobs ausführt.

```
Besucher ──► velox-cut.de (Vercel) ──► Supabase (Konten, Datenbank, Videos)
                  │                         ▲
                  └──► Stripe (Karte, Abos, Nachkauf) ── Webhook ──┘
Schnitt-Maschine (Mac) ◄── holt Aufträge ── Supabase ── lädt Clips hoch ──►
```

## 1. Konten anlegen (einmalig)

| Dienst | Wofür | Kosten zum Start |
|---|---|---|
| GitHub | Code-Ablage, Vercel baut daraus | 0 € |
| Vercel | Website + App | 0 € (Hobby); für Firmen-Nutzung **Pro 20 $/Monat** |
| Supabase | Login, Datenbank, Video-Speicher | **Pro 25 $/Monat** nötig (Free erlaubt nur Dateien bis 50 MB) |
| Stripe | Karte, Abos, Nachkauf | nur Gebühren pro Zahlung |
| Resend | Bestätigungs-E-Mails | 0 € bis 3.000 Mails/Monat |
| Google AI Studio | Gemini-Schlüssel (KI-Momentsuche, Schritt 2) | wenige Cent pro Video |

## 2. Supabase

1. In Vercel: **Storage → Supabase → Create**. Region **Frankfurt (eu-central-1)** wählen – steht so in der Datenschutzerklärung.
   Dadurch werden `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` und `SUPABASE_SERVICE_ROLE_KEY` automatisch gesetzt.
2. Supabase-Dashboard → **SQL Editor** → Inhalt von `supabase/migrations/0001_init.sql` einfügen → **Run**.
3. **Authentication → URL Configuration**
   - Site URL: `https://velox-cut.de`
   - Redirect URLs: `https://velox-cut.de/auth/callback`, `http://localhost:3010/auth/callback`
4. **Authentication → Emails → SMTP Settings**: Resend eintragen (Host `smtp.resend.com`, Port 465, User `resend`,
   Passwort = Resend-API-Key, Absender `noreply@velox-cut.de`). **Ohne eigenes SMTP verschickt Supabase keine Mails an echte Kunden.**
   Die Mail-Vorlagen dort auf Deutsch umstellen.
5. **Storage → Settings**: maximale Dateigröße auf **2 GB** setzen (nur mit Pro-Plan möglich).

## 3. Stripe

1. Konto verifizieren (Ausweis, Bankkonto). Unter Steuern angeben: **Kleinunternehmer, keine Umsatzsteuer**.
2. **Einstellungen → Rechnungen → Standard-Fußzeile**: `Gemäß § 19 UStG wird keine Umsatzsteuer berechnet.`
3. Zuerst im **Testmodus** arbeiten. Preise anlegen (einmalig, im Terminal im Projektordner):
   ```
   STRIPE_SECRET_KEY=sk_test_… node scripts/stripe-setup.mjs
   ```
4. **Entwickler → Webhooks → Endpoint hinzufügen**: `https://velox-cut.de/api/stripe/webhook`
   Ereignisse: `invoice.paid`, `customer.subscription.created`, `customer.subscription.updated`,
   `customer.subscription.deleted`, `payment_intent.succeeded`, `setup_intent.succeeded`.
   Den **Signing secret** (`whsec_…`) als `STRIPE_WEBHOOK_SECRET` in Vercel eintragen.
5. **Einstellungen → Kundenportal** aktivieren: Rechnungen anzeigen, Zahlungsmethode ändern, Paketwechsel (Starter/Pro/Business).
   Kündigen läuft über unseren eigenen Button „Verträge hier kündigen“.
6. **Beta-Preisgarantie**: Bei einer späteren Preiserhöhung **neue** Preise mit neuem Lookup-Key anlegen – bestehende Abos
   behalten ihren alten Preis automatisch. Alte Preise nie ändern oder löschen.

Test-Karten: `4242 4242 4242 4242` (klappt), `4000 0027 6000 3184` (verlangt 3-D Secure), `4000 0000 0000 9995` (abgelehnt).

## 4. Resend

Domain `velox-cut.de` hinzufügen, die angezeigten DNS-Einträge beim Domain-Anbieter eintragen, API-Key erstellen.

## 5. Vercel

1. GitHub-Repository mit diesem Ordner verbinden (oder `npx vercel` im Projektordner).
2. **Environment Variables** – siehe `.env.example` (Stripe, Resend, OWNER_EMAIL; Supabase kommt aus Schritt 2).
3. **Domains**: `velox-cut.de` und `www.velox-cut.de` hinzufügen und die DNS-Einträge beim Domain-Anbieter setzen
   (die alte Netlify-Seite danach löschen).

## 6. Schnitt-Maschine starten (Mac)

```
cp worker/.env.example worker/.env      # Werte eintragen (Supabase URL + service_role-Key)
node --env-file=worker/.env worker/worker.mjs
```
Solange dieses Fenster läuft, werden Aufträge geschnitten. Später zieht die Maschine auf einen Server um.

**Stand:** `worker/pipeline.py` ist noch die **provisorische** Version (Hochformat-Zuschnitt ohne KI). Schritt 2 ersetzt sie
durch die echte Clean-/Viral-Pipeline (mlx-whisper + Gemini + Remotion).

## 7. Website aktualisieren

Die Website-Quelle liegt im Ordner `webseite` neben `app` (SSD: Ordner `Velox Cut`). Nach Änderungen: `scripts/sync-site.sh` ausführen, dann deployen.

## 8. Vor dem Live-Gang

- [ ] Testkauf mit Testkarten: Karte speichern, Abo, Nachkauf, 3-D Secure, Kündigung → Credits & E-Mails prüfen
- [ ] Rechtstexte (AGB, Datenschutz, Widerruf) von Anwalt/IT-Recht Kanzlei prüfen lassen
- [ ] Stripe auf **Live-Schlüssel** umstellen, Webhook im Live-Modus neu anlegen, `stripe-setup.mjs` mit Live-Key ausführen
- [ ] Website-Buttons („Kostenlos starten“, „Beta-Zugang“, „Mit … loslegen“) auf `/registrieren` umstellen
- [ ] Echte Schnitt-Pipeline (Schritt 2) fertig und getestet
