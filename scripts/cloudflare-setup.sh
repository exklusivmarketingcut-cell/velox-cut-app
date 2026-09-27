#!/bin/bash
# Einmalige Einrichtung: fragt alle Schlüssel ab (unsichtbare Eingabe) und verteilt sie an
#   - Cloudflare (Secrets der App), - .env.production.local (öffentliche Werte für den Build),
#   - worker/.env (Schnitt-Maschine auf dem Mac), und richtet auf Wunsch die Datenbank ein.
# Schlüssel landen NIE in Git und nie im Chat.
set -e
cd "$(dirname "$0")/.."
ask()  { local v; read -rp "$1: " v; echo "$v"; }
askh() { local v more; read -rsp "$1: " v; while IFS= read -rs -t 1 more; do v+="$more"; done; echo "" >&2; printf '%s' "$v" | tr -d '[:space:]'; }
need() { [[ "$1" == $2 ]] || { echo "✗ $3 sieht nicht richtig aus (erwartet: $2). Bitte Programm neu starten." >&2; exit 1; }; }

echo ""
echo "════ Velox Cut – Einrichtung auf Cloudflare ════"
echo "Tipp: Bei den versteckten Eingaben siehst du beim Einfügen nichts – das ist normal. Einfügen mit Cmd+V, dann Enter."
echo ""
echo "── Supabase (supabase.com → dein Projekt → Project Settings → API Keys / Data API) ──"
SB_URL=$(ask "1) Project URL (https://….supabase.co)")
# auch die Dashboard-Adresse (supabase.com/dashboard/project/<id>) akzeptieren
if [[ "$SB_URL" =~ supabase\.com/dashboard/project/([a-z0-9]+) ]]; then SB_URL="https://${BASH_REMATCH[1]}.supabase.co"; fi
SB_URL="${SB_URL%/}"
need "$SB_URL" "https://*.supabase.co" "Die Project URL"
echo "   → $SB_URL"
SB_ANON=$(askh "2) anon / publishable key");                     [[ -n "$SB_ANON" ]] || exit 1
SB_SERVICE=$(askh "3) Secret key (sb_secret_… – NICHT der Publishable key)")
[[ "$SB_SERVICE" == sb_secret_* || "$SB_SERVICE" == eyJ* ]] && [[ "$SB_SERVICE" != "$SB_ANON" ]] || { echo "✗ Das ist kein Secret key (muss mit sb_secret_ beginnen). Bitte neu starten."; exit 1; }
echo ""
echo "── Stripe (Sandbox „Velox Cut Studio“ → Entwickler/innen → API-Schlüssel) ──"
PK=$(askh "4) Veröffentlichbarer Schlüssel (pk_test_…)");        need "$PK" "pk_test_*" "Der Veröffentlichbare Schlüssel"
SK=$(askh "5) Geheimschlüssel (sk_test_…)");                     [[ "$SK" == sk_test_* || "$SK" == rk_test_* ]] || { echo "✗ Kein sk_test_-Schlüssel."; exit 1; }
echo ""
echo "── Cloudflare R2 (R2 → API-Token verwalten → Token mit „Object Read & Write“) ──"
R2_ID=$(askh "6) Access Key ID");                                 [[ -n "$R2_ID" ]] || exit 1
R2_SECRET=$(askh "7) Secret Access Key");                         [[ -n "$R2_SECRET" ]] || exit 1

echo ""
echo "Speichere …"
# öffentliche Werte für den Build (landen ohnehin im Browser)
cat > .env.production.local <<ENV
NEXT_PUBLIC_SUPABASE_URL=$SB_URL
NEXT_PUBLIC_SUPABASE_ANON_KEY=$SB_ANON
NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=$PK
ENV
echo "✓ öffentliche Werte für die App"

# Schnitt-Maschine
ACC=$(grep -o '"R2_ACCOUNT_ID": "[^"]*"' wrangler.jsonc | cut -d'"' -f4)
GEM=$(grep -s '^GEMINI_API_KEY=' worker/.env | cut -d= -f2-)
cat > worker/.env <<ENV
SUPABASE_URL=$SB_URL
SUPABASE_SERVICE_ROLE_KEY=$SB_SERVICE
WORKER_NAME=mac-nunu
R2_ACCOUNT_ID=$ACC
R2_BUCKET=veloxcut-videos
R2_ENDPOINT=https://$ACC.eu.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=$R2_ID
R2_SECRET_ACCESS_KEY=$R2_SECRET
GEMINI_API_KEY=$GEM
ENV
chmod 600 worker/.env .env.production.local
echo "✓ Schnitt-Maschine (worker/.env)"

# Geheime Werte als Cloudflare-Secrets (JSON über stdin, nichts auf der Festplatte)
node -e 'process.stdout.write(JSON.stringify({SUPABASE_SERVICE_ROLE_KEY:process.argv[1],STRIPE_SECRET_KEY:process.argv[2],R2_ACCESS_KEY_ID:process.argv[3],R2_SECRET_ACCESS_KEY:process.argv[4]}))' \
  "$SB_SERVICE" "$SK" "$R2_ID" "$R2_SECRET" | npx wrangler secret bulk >/dev/null 2>&1 \
  && echo "✓ Cloudflare-Secrets" || echo "⚠ Cloudflare-Secrets noch nicht gesetzt (App erst veröffentlichen) – Claude erledigt das danach."
unset SB_SERVICE SK R2_ID R2_SECRET

echo ""
echo "── Datenbank einrichten (Tabellen, Credit-Funktionen) ──"
read -rp "Soll die Datenbank jetzt eingerichtet werden? (j/n): " OK
if [[ "$OK" == j* || "$OK" == J* ]]; then
  echo "Supabase → oben „Connect“ → „Session pooler“ → die URI (postgresql://…) kopieren."
  DBURL=$(askh "8) Session-pooler-URI")
  DBPW=$(askh "9) Datenbank-Passwort (das du beim Anlegen vergeben hast)")
  # Passwort URL-sicher einsetzen (Sonderzeichen wie @ oder : würden die Adresse sonst zerstören)
  DBPW_ENC=$(node -e 'process.stdout.write(encodeURIComponent(process.argv[1]))' "$DBPW")
  DBURL="${DBURL//\[YOUR-PASSWORD\]/$DBPW_ENC}"
  DATABASE_URL="$DBURL" node scripts/db-migrate.mjs
  unset DBURL DBPW DBPW_ENC
fi
echo ""
echo "Fertig. Du kannst Claude Bescheid sagen."
