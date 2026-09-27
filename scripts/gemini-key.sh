#!/bin/bash
# Gemini-API-Schlüssel für die Schnitt-Maschine eintragen (worker/.env). Liest aus der Zwischenablage.
set -e
cd "$(dirname "$0")/.."
echo ""
echo "1. aistudio.google.com → API-Schlüssel → Schlüssel kopieren (Abrechnung muss aktiviert sein)"
echo "2. Dann hier nur Enter drücken."
read -rs _
K="$(pbpaste | tr -d '[:space:]')"
[[ "$K" == AIza* || ${#K} -ge 30 ]] || { echo "✗ In der Zwischenablage ist kein Gemini-Schlüssel. Bitte kopieren und neu starten."; exit 1; }
node -e 'const fs=require("fs");const p="worker/.env";let s=fs.readFileSync(p,"utf8");s=/^GEMINI_API_KEY=.*$/m.test(s)?s.replace(/^GEMINI_API_KEY=.*$/m,"GEMINI_API_KEY="+process.argv[1]):s+"\nGEMINI_API_KEY="+process.argv[1]+"\n";fs.writeFileSync(p,s)' "$K"
printf '' | pbcopy
unset K
echo "✓ Gemini-Schlüssel gespeichert (Zwischenablage geleert). Sag Claude Bescheid."
