#!/bin/bash
# Stripe-Webhook-Signaturschlüssel sicher in Vercel eintragen (alle 3 Umgebungen). Eingabe unsichtbar.
set -e
cd "$(dirname "$0")/.."
echo ""
echo "Stripe-Webhook-Schlüssel für Velox Cut in Vercel eintragen"
echo "(Stripe → Webhooks → dein Endpunkt → Signaturgeheimnis. Beim Einfügen siehst du nichts – das ist normal.)"
echo ""
read -rsp "Signaturgeheimnis einfügen (whsec_…) und Enter: " WH; echo ""
[[ "$WH" == whsec_* ]] || { echo "✗ Das ist kein whsec_-Schlüssel. Bitte neu starten."; exit 1; }
for ENV in production preview development; do
  printf '%s' "$WH" | npx --yes vercel@latest env add STRIPE_WEBHOOK_SECRET "$ENV" --no-sensitive --force --yes >/dev/null 2>&1
  echo "✓ $ENV"
done
unset WH
echo ""
echo "Fertig – der Webhook-Schlüssel ist in Vercel. Du kannst Claude Bescheid sagen."
