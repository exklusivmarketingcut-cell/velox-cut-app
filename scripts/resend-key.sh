#!/bin/bash
# Resend-API-Schlüssel (re_…) als Cloudflare-Secret setzen (App-E-Mails wie die Kündigungsbestätigung). Liest aus der Zwischenablage.
set -e
cd "$(dirname "$0")/.."
echo ""
echo "1. resend.com → API Keys → deinen Schlüssel „Velox Cut“ kopieren"
echo "2. Dann hier nur Enter drücken (nichts einfügen)."
read -rs _
K="$(pbpaste | tr -d '[:space:]')"
[[ "$K" == re_* ]] || { echo "✗ In der Zwischenablage ist kein Resend-Schlüssel (beginnt mit „${K:0:4}…“). Bitte nochmal kopieren und neu starten."; exit 1; }
echo "Gefunden: re_… (${#K} Zeichen)"
node -e 'process.stdout.write(JSON.stringify({RESEND_API_KEY:process.argv[1]}))' "$K" | npx wrangler secret bulk >/dev/null 2>&1 && echo "✓ Cloudflare"
printf '' | pbcopy
unset K
echo "Fertig – Zwischenablage geleert. Sag Claude Bescheid."
