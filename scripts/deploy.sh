#!/bin/zsh
# Velox Cut – App + Webseite bauen und bei Cloudflare veröffentlichen.
#   zsh scripts/deploy.sh
# Die Quelle liegt auf der SSD (exFAT). Dort kann Next.js nicht bauen: macOS legt zu jeder neu geschriebenen Datei
# eine „._…“-Hilfsdatei an, daran scheitert der Build (und die Dateien würden mit hochgeladen).
# Deshalb wird in einem Zwischenordner auf dem Mac gebaut – nur ein Zwischenspeicher, darf jederzeit gelöscht werden.
set -e
APP="${0:A:h:h}"
BUILD="$HOME/Library/Caches/velox-cut-build"
mkdir -p "$BUILD"
rsync -a --delete --exclude node_modules --exclude .next --exclude .open-next --exclude .wrangler --exclude .git \
  --exclude '._*' --exclude '.DS_Store' "$APP/" "$BUILD/"
cd "$BUILD"
if [ ! -d node_modules ] || [ package-lock.json -nt node_modules/.package-lock.json ]; then
  npm ci --no-audit --no-fund
fi
npx opennextjs-cloudflare build
npx opennextjs-cloudflare deploy
