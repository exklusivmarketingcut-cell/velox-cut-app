#!/bin/bash
# Stripe-Webhook-Signaturgeheimnis (whsec_…) als Cloudflare-Secret setzen. Liest aus der Zwischenablage.
set -e
cd "$(dirname "$0")/.."
echo ""
echo "1. Stripe → Webhooks → dein Endpunkt → „Signaturgeheimnis“ anzeigen → kopieren"
echo "2. Dann hier nur Enter drücken (nichts einfügen)."
read -rs _
K="$(pbpaste | tr -d '[:space:]')"
[[ "$K" == whsec_* ]] || { echo "✗ In der Zwischenablage ist kein Webhook-Geheimnis (beginnt mit „${K:0:8}…“). Bitte nochmal kopieren und neu starten."; exit 1; }
echo "Gefunden: whsec_… (${#K} Zeichen)"
node -e 'process.stdout.write(JSON.stringify({STRIPE_WEBHOOK_SECRET:process.argv[1]}))' "$K" | npx wrangler secret bulk >/dev/null 2>&1 && echo "✓ Cloudflare"
printf '' | pbcopy
unset K
echo "Fertig – Zwischenablage geleert. Sag Claude Bescheid."
