#!/bin/zsh
# Velox Cut – Testlauf: schneidet Test-Videos lokal (ohne Credits) und legt die Ergebnisse in schnitt-maschine/test/ergebnisse.
# Aufruf: zsh scripts/testlauf.sh "Quelle|stil|untertitel|name" ["…" …]
APP="${0:A:h:h}"; M="${APP:h}/schnitt-maschine"; OUT="$M/test/ergebnisse"; WORK="$M/tmp/testlauf"
mkdir -p "$OUT" "$WORK"
set -a; . "$APP/worker/.env"; set +a
export VELOX_JOBLOG="$M/test/jobs_test.jsonl" PYTHONUNBUFFERED=1
for spec in "$@"; do
  IFS='|' read -r src style cap name <<< "$spec"
  d="$WORK/$name"; rm -rf "$d"; mkdir -p "$d"
  echo "▶ $name ($style/$cap) $(date +%T)"
  t0=$(date +%s)
  "$M/venv/bin/python" "$APP/worker/pipeline.py" --src "$src" --out "$d" --style "$style" --caption "$cap" --keep-work > "$d/log.txt" 2>&1
  rc=$?
  grep -v -E "^PROGRESS|Warning|Fetching" "$d/log.txt" | grep -E "Story|raus:|Clip |Nachgehört|Hörprobe|Qualit|Gemini|Error|Fehler|Traceback" | head -25
  if [ $rc -eq 0 ] && ls "$d"/clip-*.mp4 >/dev/null 2>&1; then
    for c in "$d"/clip-*.mp4; do cp "$c" "$OUT/${name}_${style}_${c:t}"; done
    echo "✓ $name fertig in $(( $(date +%s) - t0 )) s"
  else
    echo "✗ $name FEHLGESCHLAGEN (rc=$rc): $(tail -2 "$d/log.txt" | tr '\n' ' ')"
  fi
done
find "$OUT" -name '._*' -delete 2>/dev/null
