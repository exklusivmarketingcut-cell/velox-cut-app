#!/bin/bash
# Nur den Supabase-Secret-Key (service_role) neu setzen: Cloudflare-Secret + worker/.env
# Liest den Schlüssel direkt aus der Zwischenablage (kein Einfügen ins Terminal → keine Zeilenumbruch-Probleme)
set -e
cd "$(dirname "$0")/.."
echo ""
echo "1. Supabase → Project Settings → API Keys → „Secret keys“ → beim NEUEN Schlüssel auf das Kopier-Symbol klicken"
echo "2. Dann hier im Terminal nur Enter drücken (nichts einfügen)."
read -rs _
K="$(pbpaste | tr -d '[:space:]')"
[[ "$K" == sb_secret_* || "$K" == eyJ* ]] || { echo "✗ In der Zwischenablage ist kein Secret key (beginnt mit „${K:0:12}…“, ${#K} Zeichen). Bitte nochmal kopieren und neu starten."; exit 1; }
echo "Gefunden: ${K:0:10}… (${#K} Zeichen)"
node -e 'process.stdout.write(JSON.stringify({SUPABASE_SERVICE_ROLE_KEY:process.argv[1]}))' "$K" | npx wrangler secret bulk >/dev/null 2>&1 && echo "✓ Cloudflare"
node -e 'const fs=require("fs");const p="worker/.env";fs.writeFileSync(p,fs.readFileSync(p,"utf8").replace(/^SUPABASE_SERVICE_ROLE_KEY=.*$/m,"SUPABASE_SERVICE_ROLE_KEY="+process.argv[1]))' "$K" && echo "✓ Schnitt-Maschine"
printf '' | pbcopy   # Zwischenablage leeren
unset K
echo "Fertig – Zwischenablage geleert. Sag Claude Bescheid."
