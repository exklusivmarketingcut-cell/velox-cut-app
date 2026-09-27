#!/bin/sh
# Übernimmt die Website (~/veloxcut-studio) als statische Startseite in public/.
set -e
SRC="${1:-$HOME/veloxcut-studio}"
DEST="$(cd "$(dirname "$0")/.." && pwd)/public"
rsync -a --delete --exclude '._*' --exclude '.claude' --exclude '.DS_Store' --exclude '_src' --exclude '_shot.html' "$SRC/" "$DEST/"
echo "Website übernommen: $SRC -> $DEST"
