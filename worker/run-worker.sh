#!/bin/zsh
# Velox Cut – startet die Schnitt-Maschine. Aufgerufen vom Starter „~/Applications/Velox Schnitt-Maschine.app“ (liegt auf dem Mac – macOS erlaubt den Festplattenvollzugriff nur Programmen auf der internen Platte)
# (launchd: beim Anmelden, nach einem Neustart und automatisch nach jedem Absturz).
# Alles liegt in einem Ordner auf der SSD:  …/app (Code)  und  …/schnitt-maschine (Python, Schriften, Protokolle)
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
APP="${0:A:h:h}"
MASCHINE="${APP:h}/schnitt-maschine"
LOG="$MASCHINE/logs/worker.log"
mkdir -p "$MASCHINE/logs" "$MASCHINE/tmp"
cd "$APP" || exit 1
echo "$(date '+%F %T') Start" >> "$LOG"
# caffeinate: Mac schläft nicht ein, solange die Schnitt-Maschine läuft (am Strom)
exec /usr/bin/caffeinate -ims /usr/local/bin/node --env-file=worker/.env worker/worker.mjs >> "$LOG" 2>&1
