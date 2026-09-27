#!/bin/sh
# Übernimmt die Website (~/veloxcut-studio) als statische Startseite in public/.
# Videos (*.mp4) kommen NICHT in public/, sondern liegen im öffentlichen R2-Bucket „veloxcut-media“:
# Cloudflare-Assets können keine Teil-Downloads (HTTP 206), die iPhones für Videos brauchen.
# Neue/geänderte Videos hochladen: scripts/upload-media.sh
set -e
SRC="${1:-$HOME/veloxcut-studio}"
DEST="$(cd "$(dirname "$0")/.." && pwd)/public"
MEDIA_URL="${MEDIA_URL:-https://pub-4248ade5d1614698af46b6f901c7bbd8.r2.dev}"
rsync -a --delete --exclude '._*' --exclude '.claude' --exclude '.DS_Store' --exclude '_src' --exclude '_shot.html' --exclude '*.mp4' --delete-excluded "$SRC/" "$DEST/"
# Video-Pfade auf den Medien-Speicher umbiegen
perl -pi -e "s#(?<![/\\w])assets/(demo|portfolio)/([\\w.-]+\\.mp4)#$MEDIA_URL/assets/\$1/\$2#g" "$DEST"/*.html
perl -pi -e "s#\`assets/demo/style_(\\\$\\{k\\}\\.mp4)\`#\`$MEDIA_URL/assets/demo/style_\$1\`#g" "$DEST/main.js"
echo "Website übernommen: $SRC -> $DEST (Videos von $MEDIA_URL)"
