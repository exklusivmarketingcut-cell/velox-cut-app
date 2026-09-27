#!/bin/bash
# Stripe-Schlüssel sicher in Vercel eintragen (alle 3 Umgebungen). Die Eingabe ist unsichtbar und landet nirgends sonst.
set -e
cd "$(dirname "$0")/.."

echo ""
echo "Stripe-Schlüssel für Velox Cut in Vercel eintragen"
echo "(Stripe → Sandbox „Velox Cut Studio“ → API-Schlüssel. Beim Einfügen siehst du nichts – das ist normal.)"
echo ""

read -rsp "1) Veröffentlichbaren Schlüssel einfügen (pk_test_…) und Enter: " PK; echo ""
[[ "$PK" == pk_test_* ]] || { echo "✗ Das ist kein pk_test_-Schlüssel. Bitte den aus der Sandbox nehmen und neu starten."; exit 1; }
read -rsp "2) Geheimschlüssel einfügen (sk_test_…) und Enter: " SK; echo ""
[[ "$SK" == sk_test_* || "$SK" == rk_test_* ]] || { echo "✗ Das ist kein sk_test_-Schlüssel. Bitte den aus der Sandbox nehmen und neu starten."; exit 1; }

echo ""
for ENV in production preview development; do
  printf '%s' "$PK" | npx --yes vercel@latest env add NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY "$ENV" --no-sensitive --force --yes >/dev/null 2>&1
  printf '%s' "$SK" | npx --yes vercel@latest env add STRIPE_SECRET_KEY "$ENV" --no-sensitive --force --yes >/dev/null 2>&1
  echo "✓ $ENV"
done
unset PK SK
echo ""
echo "Fertig – beide Stripe-Schlüssel sind in Vercel. Du kannst dieses Fenster schließen und Claude Bescheid sagen."
