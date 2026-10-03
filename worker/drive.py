"""Velox Cut – öffentlichen Google-Drive-ORDNER herunterladen (Videos + Bilder, auch Unterordner).

Braucht keinen API-Schlüssel: der Ordner muss nur „Jeder mit dem Link“ freigegeben sein (gdown liest die öffentliche
Ordner-Ansicht). Drive zeigt pro Ordner höchstens 50 Dateien.

  python drive.py <ordner-link> <ziel-ordner>
Ausgabe (letzte Zeile, JSON): {"files": n} – oder Fehlertext auf stderr.
"""
import json, os, sys

import gdown

url, out = sys.argv[1], sys.argv[2]
MEDIA = (".mp4", ".mov", ".m4v", ".mts", ".avi", ".mkv", ".webm", ".jpg", ".jpeg", ".png", ".webp", ".heic")
MAX = 80
os.makedirs(out, exist_ok=True)
try:
    items = gdown.download_folder(url, skip_download=True, quiet=True, use_cookies=False) or []
except Exception as e:  # noqa: BLE001
    sys.exit("Drive-Ordner nicht lesbar – ist er für „Jeder mit dem Link“ freigegeben? (" + str(e).splitlines()[0][:120] + ")")
items = [x for x in items if x.path.lower().endswith(MEDIA) and not os.path.basename(x.path).startswith(".")]
if not items:
    sys.exit("Im Drive-Ordner sind keine Videos oder Bilder (oder er ist nicht für „Jeder mit dem Link“ freigegeben).")
if len(items) > MAX:
    sys.exit(f"Der Drive-Ordner hat {len(items)} Dateien – bitte höchstens {MAX} auf einmal.")
n = 0
for k, x in enumerate(items):
    name = f"{k:03d}_" + x.path.replace("/", "_")  # Unterordner flach, Reihenfolge bleibt
    print(f"STAGE Lädt aus Google Drive ({k + 1}/{len(items)}) …", flush=True)
    if gdown.download(id=x.id, output=os.path.join(out, name), quiet=True, use_cookies=False):
        n += 1
if not n:
    sys.exit("Aus dem Drive-Ordner ließ sich keine Datei laden.")
print(json.dumps({"files": n}), flush=True)
