"""Velox Cut – Prüfstand: Rohclips aus den Vorher-Nachher-Paaren des Nutzers mit der Pipeline schneiden und mit SEINEM
fertigen Schnitt vergleichen (Länge, welche Wörter drin sind). So sieht man nach jeder Änderung, ob es besser wird.

  venv/bin/python app/worker/pruefstand.py <label> [anzahl] [--review]
Ergebnis: schnitt-maschine/test/pruefstand/<label>/ergebnis.json + Tabelle in der Konsole."""
import difflib, json, os, re, subprocess, sys, time
import mlx_whisper

HERE = os.path.dirname(os.path.abspath(__file__))
MASCHINE = os.path.join(HERE, "..", "..", "schnitt-maschine")
PAARE = os.path.join(HERE, "..", "..", "material", "vorher-nachher", "analyse", "em", "pruefstand.json")
TR = os.path.join(HERE, "..", "..", "material", "vorher-nachher", "analyse", "em", "transkripte")
label = sys.argv[1]
n = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 10
review = "--review" in sys.argv
out_dir = os.path.join(MASCHINE, "test", "pruefstand", label)
os.makedirs(out_dir, exist_ok=True)
norm = lambda s: re.sub(r"[^\wäöüß]", "", s.lower())
pairs = [p for p in json.load(open(PAARE)) if 30 <= p["roh_s"] <= 100][:n]  # typische Umfragen (Rohclip 30–100 s)
only = [x for x in os.environ.get("PF_ONLY", "").split(",") if x]  # nur bestimmte Paare (z. B. PF_ONLY=umfrage_5,umfrage_12)
if only:
    pairs = [p for p in pairs if any(os.path.basename(p["fertig"]).rsplit(".", 1)[0].strip().replace(" ", "_").endswith(o) for o in only)]
env = {**os.environ, "VELOX_JOBLOG": os.path.join(out_dir, "jobs.jsonl")}
env["GEMINI_API_KEY"] = [l.split("=", 1)[1].strip() for l in open(os.path.join(HERE, ".env")) if l.startswith("GEMINI_API_KEY=")][0]


def tr_name(path, kind, proj):
    return os.path.join(TR, f"{proj}__{kind}__{os.path.basename(path).rsplit('.', 1)[0].strip().replace(' ', '_')}.json")


def kept_idx(words, raw):
    rn = [norm(w["t"]) for w in raw]
    k = set()
    for a, b, m in difflib.SequenceMatcher(None, [norm(w) for w in words], rn, autojunk=False).get_matching_blocks():
        if m >= 2:
            k.update(range(b, b + m))
    return k


rows = []
for p in pairs:
    name = os.path.basename(p["fertig"]).rsplit(".", 1)[0].strip().replace(" ", "_")
    d = os.path.join(out_dir, f"{p['proj']}__{name}")
    clip = os.path.join(d, "clip-1.mp4")
    t0 = time.time()
    if not os.path.exists(clip):
        os.makedirs(d, exist_ok=True)
        args = [os.path.join(MASCHINE, "venv", "bin", "python"), os.path.join(HERE, "pipeline.py"), "--src", p["roh"], "--out", d,
                "--style", "clean", "--caption", "clean"] + ([] if review else ["--no-review"])
        r = subprocess.run(args, env=env, capture_output=True, text=True)
        if r.returncode != 0:
            print(name, "FEHLER", (r.stderr or r.stdout).strip().splitlines()[-1:], flush=True)
            continue
    raw = json.load(open(tr_name(p["roh"], "raw", p["proj"])))["words"]
    user = json.load(open(tr_name(p["fertig"], "fertig", p["proj"])))["words"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", clip, "-vn", "-ac", "1", "-ar", "16000", "/tmp/pf.wav"], check=True)
    rr = mlx_whisper.transcribe("/tmp/pf.wav", path_or_hf_repo="mlx-community/whisper-large-v3-turbo", word_timestamps=True,
                                condition_on_previous_text=False, language="de")
    ours = [w["word"].strip() for s in rr["segments"] for w in s.get("words", [])]
    U, O = kept_idx([w["t"] for w in user], raw), kept_idx(ours, raw)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", clip],
                               capture_output=True, text=True).stdout)
    prec = len(U & O) / max(1, len(O))   # wie viel von unserem Schnitt hätte der Nutzer auch drin
    rec = len(U & O) / max(1, len(U))    # wie viel vom Nutzer-Schnitt ist bei uns drin
    row = {"name": name, "roh_s": p["roh_s"], "nutzer_s": p["fertig_s"], "wir_s": round(dur, 1), "precision": round(prec, 2),
           "recall": round(rec, 2), "zu_viel": [raw[j]["t"] for j in sorted(O - U)][:40], "fehlt": [raw[j]["t"] for j in sorted(U - O)][:40]}
    rows.append(row)
    print(f"{name[:26]:26s} roh {p['roh_s']:5.1f}  Nutzer {p['fertig_s']:5.1f}  wir {dur:5.1f}  Precision {prec:.2f}  Recall {rec:.2f}  ({time.time() - t0:.0f} s)", flush=True)
if rows:
    avg = lambda k: sum(r[k] for r in rows) / len(rows)
    summ = {"label": label, "paare": len(rows), "laenge_wir_zu_nutzer": round(sum(r["wir_s"] for r in rows) / sum(r["nutzer_s"] for r in rows), 2),
            "precision": round(avg("precision"), 3), "recall": round(avg("recall"), 3)}
    summ["f1"] = round(2 * summ["precision"] * summ["recall"] / max(1e-9, summ["precision"] + summ["recall"]), 3)
    print("GESAMT", summ, flush=True)
    json.dump({"summary": summ, "rows": rows}, open(os.path.join(out_dir, "ergebnis.json"), "w"), ensure_ascii=False, indent=1)
