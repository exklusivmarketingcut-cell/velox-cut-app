#!/bin/sh
# Alle Website-Videos in den öffentlichen R2-Bucket „veloxcut-media“ hochladen (überschreibt vorhandene).
set -e
cd "${1:-$HOME/veloxcut-studio}"
for f in $(find assets -name "*.mp4" | sort); do
  npx wrangler r2 object put "veloxcut-media/$f" --file "$f" --content-type video/mp4 --cache-control "public, max-age=604800" --remote >/dev/null && echo "✓ $f"
done
