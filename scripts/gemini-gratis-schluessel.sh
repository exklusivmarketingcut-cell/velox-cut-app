#!/bin/zsh
# Kostenlosen Gemini-Schlüssel einrichten (Projekt OHNE Zahlungsdaten → Gratis-Kontingent).
# Liest den Schlüssel aus der Zwischenablage, prüft ihn, trägt ihn in worker/.env ein (GEMINI_API_KEY_FREE)
# und startet die Schnitt-Maschine neu. Der bisherige Prepay-Schlüssel bleibt als Reserve.
APP="${0:A:h:h}"
ENV="$APP/worker/.env"
echo ""
echo "1. aistudio.google.com/apikey öffnen"
echo "2. „API-Schlüssel erstellen“ → „In neuem Projekt erstellen“ (KEIN Projekt mit Zahlungsdaten!)"
echo "3. Den neuen Schlüssel kopieren (beginnt mit AIza…)"
echo "4. Dann hier nur Enter drücken (nichts einfügen)."
read -rs _
K="$(pbpaste | tr -d '[:space:]')"
[[ "$K" == AIza* && ${#K} -ge 30 ]] || { echo "✗ In der Zwischenablage ist kein Gemini-Schlüssel (beginnt mit „${K:0:4}…“). Bitte nochmal kopieren und neu starten."; exit 1; }
echo "Gefunden: AIza… (${#K} Zeichen) – teste …"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "x-goog-api-key: $K" -H "Content-Type: application/json" \
  -d '{"contents":[{"parts":[{"text":"ok"}]}],"generationConfig":{"maxOutputTokens":1}}' \
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-lite-latest:generateContent")
case "$CODE" in
  200|429) echo "✓ Schlüssel funktioniert (HTTP $CODE)";;
  402) echo "✗ Dieser Schlüssel gehört zu einem Projekt mit Prepay/Zahlungsdaten (Guthaben leer). Bitte einen Schlüssel in einem NEUEN Projekt erstellen."; printf '' | pbcopy; exit 1;;
  *) echo "✗ Google lehnt den Schlüssel ab (HTTP $CODE). Bitte nochmal kopieren."; printf '' | pbcopy; exit 1;;
esac
if grep -q '^GEMINI_API_KEY_FREE=' "$ENV" 2>/dev/null; then
  /usr/bin/sed -i '' "s|^GEMINI_API_KEY_FREE=.*|GEMINI_API_KEY_FREE=$K|" "$ENV"
else
  printf '\n# Kostenloser Gemini-Schlüssel (Projekt ohne Zahlungsdaten) – wird zuerst genutzt\nGEMINI_API_KEY_FREE=%s\n' "$K" >> "$ENV"
fi
printf '' | pbcopy
unset K
echo "✓ Eingetragen. Schnitt-Maschine startet neu …"
pkill -f "caffeinate -ims /usr/local/bin/node --env-file=worker/.env" 2>/dev/null
pkill -f "node --env-file=worker/.env worker/worker.mjs" 2>/dev/null
sleep 8
pgrep -f "node --env-file=worker/.env worker/worker.mjs" >/dev/null && echo "✓ Läuft wieder. Fertig – Zwischenablage geleert. Sag Claude Bescheid." \
  || echo "⚠︎ Schnitt-Maschine noch nicht wieder da – sie startet automatisch nach. Sag Claude Bescheid."
