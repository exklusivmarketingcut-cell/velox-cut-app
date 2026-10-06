"""Velox Cut – Prüfstand für die anderen Formate (Ergänzung zu pruefstand.py, das einzelne Umfragen prüft):

  ordner  – mehrere Rohclips (+ Bilder) → montage.py + pipeline.py; Vergleich mit dem fertigen Video des Nutzers
            (Länge, welche Wörter drin sind, Zwischenschnitte/Bilder, KI-Abnahme-Note)
  musik   – nur B-Roll → musikschnitt.py; geprüft wird: läuft durch, Länge 8–25 s, Schnitte sitzen auf dem Beat

  venv/bin/python app/worker/pruefstand_formate.py <label> [name ...]
Ergebnis: schnitt-maschine/test/pruefstand_formate/<label>/ergebnis.json + Tabelle in der Konsole."""
import difflib, json, os, re, subprocess, sys, time
import mlx_whisper

HERE = os.path.dirname(os.path.abspath(__file__))
MASCHINE = os.path.join(HERE, "..", "..", "schnitt-maschine")
PY = os.path.join(MASCHINE, "venv", "bin", "python")
EM = "/Volumes/PortableSSD/novus media/kunden-rohmaterial"
MAT = os.path.join(HERE, "..", "..", "material", "vorher-nachher")
FAELLE = [  # Eingangsordner liegen als Verknüpfungen auf die Rohclips in schnitt-maschine/test/
    {"name": "cheesecake_neue_cancake", "art": "ordner", "eingang": "test/montage1/eingang",
     "fertig": f"{EM}/Cheesecake/28 dezember/fertig/cheesecake neue cancake.mp4"},
    {"name": "malos_junior_burger", "art": "ordner", "eingang": "test/montage2/eingang",
     "fertig": f"{MAT}/malos-beispiel/fertig/malos junior burger.mp4"},
    {"name": "goldenthai_broll", "art": "musik", "eingang": "test/musik1/eingang"},
]
label = sys.argv[1]
only = sys.argv[2:]
out_dir = os.path.join(MASCHINE, "test", "pruefstand_formate", label)
os.makedirs(out_dir, exist_ok=True)
env = {**os.environ, "VELOX_JOBLOG": os.path.join(out_dir, "jobs.jsonl")}
env["GEMINI_API_KEY"] = [l.split("=", 1)[1].strip() for l in open(os.path.join(HERE, ".env")) if l.startswith("GEMINI_API_KEY=")][0]
norm = lambda s: re.sub(r"[^\wäöüß]", "", s.lower())


def py(args):
    r = subprocess.run([PY, *args], env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(((r.stderr or r.stdout).strip().splitlines() or ["?"])[-1])
    return json.loads(r.stdout.strip().splitlines()[-1])


def words(path, cache=None):
    if cache and os.path.exists(cache):
        return json.load(open(cache))
    wav = os.path.join(out_dir, "pf.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vn", "-ac", "1", "-ar", "16000", wav], check=True)
    r = mlx_whisper.transcribe(wav, path_or_hf_repo="mlx-community/whisper-large-v3-turbo", word_timestamps=True,
                               condition_on_previous_text=False, language="de")
    os.remove(wav)
    ws = [norm(w["word"]) for s in r["segments"] for w in s.get("words", []) if norm(w["word"])]
    if cache:
        json.dump(ws, open(cache, "w"))
    return ws


def dur(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                                capture_output=True, text=True).stdout)


def last_log():
    try:
        return json.loads(open(env["VELOX_JOBLOG"]).read().strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        return {}


rows = []
for f in FAELLE:
    if only and f["name"] not in only:
        continue
    d = os.path.join(out_dir, f["name"])
    os.makedirs(d, exist_ok=True)
    eingang = os.path.join(MASCHINE, f["eingang"])
    t0 = time.time()
    try:
        if f["art"] == "musik":
            res = py([os.path.join(HERE, "musikschnitt.py"), "--out", d, "--style", "viral", eingang])
            clip = res["clips"][0]["file"]
            lg = last_log()
            # Schnitte finden (Bildwechsel) und prüfen, ob sie auf dem Beat-Raster liegen
            sc = subprocess.run(["ffmpeg", "-i", clip, "-vf", "select='gt(scene,0.25)',metadata=print", "-an", "-f", "null", "-"],
                                capture_output=True, text=True).stderr
            cuts = [float(x) for x in re.findall(r"pts_time:([\d.]+)", sc)]
            beat = 60 / lg.get("bpm", 120)
            off = [min(c % beat, beat - c % beat) for c in cuts]
            on_beat = sum(o < 0.07 for o in off) / max(1, len(off))
            row = {"name": f["name"], "art": "musik", "wir_s": round(dur(clip), 1), "bilder": lg.get("bilder"), "bpm": lg.get("bpm"),
                   "musik": lg.get("music"), "auf_beat": round(on_beat, 2), "titel": lg.get("titel"),
                   "ok": 8 <= dur(clip) <= 25 and on_beat >= 0.8}
            print(f"{f['name']:26s} musik  {row['wir_s']:5.1f} s  {row['bilder']} Bilder  {row['bpm']} BPM  auf Beat {on_beat:.0%}  "
                  f"{'OK' if row['ok'] else 'PRÜFEN'}  ({time.time() - t0:.0f} s)", flush=True)
        else:
            m = py([os.path.join(HERE, "montage.py"), "--out", os.path.join(d, "montage"), eingang])
            res = py([os.path.join(HERE, "pipeline.py"), "--src", m["src"], "--plan", m["plan"], "--out", d, "--style", "viral",
                      "--caption", "hormozi"])
            clip = res["clips"][0]["file"]
            lg = last_log()
            plan = json.load(open(m["plan"]))
            U = words(f["fertig"], os.path.join(out_dir, "..", f"{f['name']}__nutzer.json"))
            O = words(clip)
            same = sum(b.size for b in difflib.SequenceMatcher(None, U, O, autojunk=False).get_matching_blocks() if b.size >= 2)
            prec, rec = same / max(1, len(O)), same / max(1, len(U))
            ab = lg.get("abnahme") or {}
            row = {"name": f["name"], "art": "ordner", "nutzer_s": round(dur(f["fertig"]), 1), "wir_s": round(dur(clip), 1),
                   "precision": round(prec, 2), "recall": round(rec, 2), "zwischenschnitte": len(plan.get("broll") or []),
                   "bilder": len(plan.get("bilder") or []), "note": ab.get("note_nachschnitt") or ab.get("note"), "titel": res["clips"][0].get("title")}
            print(f"{f['name']:26s} ordner Nutzer {row['nutzer_s']:5.1f}  wir {row['wir_s']:5.1f}  Precision {prec:.2f}  Recall {rec:.2f}  "
                  f"B-Roll {row['zwischenschnitte']}  Bilder {row['bilder']}  Note {row['note']}  ({time.time() - t0:.0f} s)", flush=True)
    except Exception as e:  # noqa: BLE001
        row = {"name": f["name"], "art": f["art"], "fehler": str(e)[:200]}
        print(f"{f['name']:26s} FEHLER {e}", flush=True)
    rows.append(row)
json.dump({"label": label, "rows": rows}, open(os.path.join(out_dir, "ergebnis.json"), "w"), ensure_ascii=False, indent=1)
