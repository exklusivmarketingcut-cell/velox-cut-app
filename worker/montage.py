"""Velox Cut – mehrere Clips (ganzer Ordner) zu EINEM Video.

Ablauf:
  1. Jeden Clip verstehen: Was wird gesagt (Whisper), was ist zu sehen (KI schaut 3 Bilder je Clip an), Qualität.
     Bilder im Ordner (Produktfotos, freigestellte Sticker) beschreibt die KI ebenfalls.
  2. Schnittplan wie ein Cutter (KI): Sprech-Clips bilden den roten Faden (welche Sätze, in welcher Reihenfolge),
     passende B-Rolls laufen über das Gesagte (z. B. Preistafel, wenn der Laden erwähnt wird), Produktbilder poppen auf,
     wenn das Produkt genannt wird.
  3. Zusammensetzen: die gewählten Sprech-Stellen hintereinander in EINE Datei (gleiches Format) → danach schneidet
     pipeline.py wie gewohnt (Pausen, Füllwörter, Tempo, Stabilisierung, Untertitel, Abnahme) und legt B-Rolls und
     Bilder an die geplanten Stellen (--plan).

  python montage.py --out <ordner> <datei oder ordner> [...]
Ausgabe (letzte Zeile, JSON): {"src": ".../montage.mp4", "plan": ".../plan.json"} – {"musik": true}, wenn niemand spricht
(dann schneidet musikschnitt.py) – oder Fehlertext auf stderr.
"""
import urllib.error
import argparse, base64, json, os, re, subprocess, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ap.add_argument("--style", default="clean")
ap.add_argument("--words", default="")
ap.add_argument("--clips", type=int, default=1, help="so viele Videos höchstens – je eigenständigem Thema im Ordner eines")
ap.add_argument("inputs", nargs="+")
A = ap.parse_args()
A.out = os.path.abspath(A.out)  # absolute Pfade (die Teile-Liste für ffmpeg concat wird relativ zu sich selbst gelesen)
os.makedirs(A.out, exist_ok=True)
VIDEO = (".mp4", ".mov", ".m4v", ".mts", ".avi", ".mkv", ".webm")
IMAGE = (".jpg", ".jpeg", ".png", ".webp", ".heic")
STATE = {}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def stage(text):
    print(f"STAGE {text}", flush=True)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1:] or cmd[0])
    return r


import ki  # noqa: E402  gemeinsamer KI-Zugang (worker/ki.py): Gratis-Schlüssel zuerst, Tageslimits umgehen

TOK = {"aufrufe": 0, "ein": 0, "aus": 0, "denken": 0, "gratis": 0}  # KI-Verbrauch (Kosten) dieses Laufs


def gemini(system, parts, schema, timeout=90, deep=False):
    """deep=True nur für den Schnittplan – sonst einfache Aufgaben auf dem sparsamen Modell."""
    res = ki.call(system, parts, schema, deep=deep, timeout=timeout, temperature=0.2)
    for k, v in list(ki.TOK.items()):
        if isinstance(v, int):
            TOK[k] = TOK.get(k, 0) + v
            ki.TOK[k] = 0
    return res


# ─────────────────────────────── 1. Dateien einsammeln ───────────────────────────────
files = []
for p in A.inputs:
    if os.path.isdir(p):
        for root, _, fs in os.walk(p):
            files += [os.path.join(root, f) for f in sorted(fs)]
    else:
        files.append(p)
files = [f for f in files if not os.path.basename(f).startswith(("._", "."))]
videos = sorted(f for f in files if f.lower().endswith(VIDEO))
images = sorted(f for f in files if f.lower().endswith(IMAGE))
if not videos:
    sys.exit("Im Ordner ist kein Video.")
log(f"{len(videos)} Videos, {len(images)} Bilder")


def probe(path):
    d = json.loads(run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate:stream_side_data=rotation:format=duration",
                        "-of", "json", path]).stdout)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    if not v:
        return None
    w, h = int(v["width"]), int(v["height"])
    rot = next((int(sd["rotation"]) for sd in (v.get("side_data_list") or []) if "rotation" in sd), 0)
    if abs(rot) in (90, 270):
        w, h = h, w
    num, den = (v.get("r_frame_rate") or "30/1").split("/")
    return {"w": w, "h": h, "fps": float(num) / float(den or 1), "dur": float(d["format"].get("duration") or 0),
            "audio": any(s["codec_type"] == "audio" for s in d["streams"])}


clips = []
for k, f in enumerate(videos):
    info = probe(f)
    if not info or info["dur"] < 0.8:
        continue
    clips.append({"id": f"C{len(clips) + 1}", "file": f, **info})
if not clips:
    sys.exit("Die Videos ließen sich nicht lesen.")

# ─────────────────────────────── 2. Was wird gesagt? (Whisper) ───────────────────────────────
stage(f"Hört sich {len(clips)} Clips an …")
import mlx_whisper  # noqa: E402

SENT_END = re.compile(r"[.!?…]$")
for c in clips:
    c["sents"] = []
    if not c["audio"]:
        continue
    wav = os.path.join(A.out, "m.wav")
    run(["ffmpeg", "-v", "error", "-y", "-i", c["file"], "-vn", "-ac", "1", "-ar", "16000", wav])
    r = mlx_whisper.transcribe(wav, path_or_hf_repo=os.environ.get("VELOX_WHISPER", "mlx-community/whisper-large-v3-turbo"),
                               word_timestamps=True, condition_on_previous_text=False, language="de",
                               hallucination_silence_threshold=2.0,
                               initial_prompt=((A.words + ". ") if A.words else "") + "Ähm, also, ja. Genau.")
    # Inhaber 08.10. (Halal-Ordner, „da fehlt ein Stück vom Video“): 40 Sekunden eines Clips kamen als EIN Satz ohne Punkt und
    # Komma zurück, klein geschrieben und verhört („ich starte ich gern bei uns zum essen ein echtes teil scharf …“) – die
    # Erkennung war dort in ihren Notbetrieb gefallen. Darin lag der ganze Schluss des Videos („Mein Angebot an dich. Ich lade
    # dich gern bei uns zum Essen ein. …“), für die Planung unsichtbar. Solche Abschnitte werden einzeln neu abgehört.
    import numpy as _np
    flach = [w for sg in r["segments"] for w in sg.get("words", []) if w["word"].strip()]
    klein = lambda w: w["word"].strip() == w["word"].strip().lower() and not re.search(r"[.!?,]", w["word"])
    n0 = 0
    while n0 < len(flach):
        if not klein(flach[n0]):
            n0 += 1
            continue
        n1 = n0   # Notbetrieb = mindestens 8 Wörter am Stück klein geschrieben und ohne jedes Satzzeichen
        while n1 + 1 < len(flach) and klein(flach[n1 + 1]) and float(flach[n1 + 1]["start"]) - float(flach[n1]["end"]) < 4.0:
            n1 += 1
        if n1 - n0 + 1 < 8:
            n0 = n1 + 1
            continue
        teil = wav[:-4] + "_teil.wav"
        # Der Notbetrieb kommt von zu leisem Ton (Golden Thai: Sprache bei −41 dB). Mit angehobenem Pegel versteht die
        # Erkennung dieselbe Stelle sauber („Mein Angebot an dich. Ich lade dich gern bei uns zum Essen ein.“). Das klappt
        # nicht bei jedem Ausschnitt auf Anhieb – deshalb mehrere Versuche (Ränder, Pegel, Satzzeichen-Vorgabe), der erste
        # mit Satzzeichen gilt. Die Ränder reichen höchstens bis an die Nachbarwörter (nichts doppelt).
        links = float(flach[n0 - 1]["end"]) + 0.05 if n0 else 0.0
        rechts = float(flach[n1 + 1]["start"]) - 0.05 if n1 + 1 < len(flach) else 1e9
        neu_w = []
        for vor, nachl, mehr, vorgabe in ((0.3, 0.3, 0, None), (0.5, 1.4, 0, None), (0.5, 1.4, 0, "Okay. Ich sage den Satz noch einmal, ganz in Ruhe."),
                                         (0.5, 1.4, -6, None), (1.0, 2.0, 4, "Okay. Ich sage den Satz noch einmal, ganz in Ruhe.")):
            a0 = max(0.0, links, float(flach[n0]["start"]) - vor)
            b0 = min(rechts, float(flach[n1]["end"]) + nachl)
            _x = _np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{a0:.2f}", "-to", f"{b0:.2f}", "-i", wav, "-f", "s16le", "-"],
                                               capture_output=True).stdout, dtype=_np.int16).astype(_np.float32) / 32768
            _rms = 20 * _np.log10(float(_np.sqrt((_x ** 2).mean())) + 1e-7) if len(_x) else -30.0
            gain = min(30.0, max(0.0, -22.0 - _rms + mehr))
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a0:.2f}", "-to", f"{b0:.2f}", "-i", wav, "-af",
                 f"volume={gain:.1f}dB,alimiter=limit=0.9", teil])
            r2 = mlx_whisper.transcribe(teil, path_or_hf_repo=os.environ.get("VELOX_WHISPER", "mlx-community/whisper-large-v3-turbo"),
                                        word_timestamps=True, condition_on_previous_text=False, language="de", initial_prompt=vorgabe)
            os.remove(teil)
            kand = [dict(w, start=float(w["start"]) + a0, end=float(w["end"]) + a0) for s2 in r2["segments"] for w in s2.get("words", []) if w["word"].strip()]
            if kand and re.search(r"[.!?]", " ".join(w["word"] for w in kand)) and len(kand) >= 0.6 * (n1 - n0 + 1):
                neu_w = kand
                break
        if neu_w and re.search(r"[.!?]", " ".join(w["word"] for w in neu_w)):
            log(f"  {c['id']}: Abschnitt {a0:.0f}–{b0:.0f} s neu abgehört (Erkennung war im Notbetrieb)")
            flach[n0:n1 + 1] = neu_w
            n0 += len(neu_w)
        else:
            n0 = n1 + 1
    ws = [{"t": w["word"].strip(), "s": float(w["start"]), "e": float(w["end"]), "p": float(w.get("probability", 1))} for w in flach]
    # Überlange Wörter (> 1 s, z. B. „machen“ 2,7 s): darin steckt oft Geflüster vom Team, das Whisper verschluckt hat.
    # Das echte Wort ist der lauteste Abschnitt; ist der Rest deutlich leiser, wird das Wort darauf gekürzt → die Lücke
    # davor/danach schneidet das Zusammensetzen dann heraus.
    import numpy as np
    pcm = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", wav, "-f", "s16le", "-"], capture_output=True).stdout,
                        dtype=np.int16).astype(np.float32)
    env = np.sqrt(np.convolve(pcm ** 2, np.ones(160) / 160, "same"))[::160] if len(pcm) else np.zeros(1)  # 10 ms
    for w in ws:
        dur = w["e"] - w["s"]
        if dur <= 1.0:
            continue
        L = max(25, int(100 * min(dur * 0.8, 0.09 * len(w["t"]) + 0.25)))
        seg = env[int(w["s"] * 100):int(w["e"] * 100)]
        if len(seg) <= L + 5:
            continue
        sums = np.convolve(seg, np.ones(L), "valid")
        j = int(np.argmax(sums))
        inside = sums[j] / L
        rest = (seg.sum() - sums[j]) / (len(seg) - L)
        if rest < 0.45 * inside:
            w["s"], w["e"] = w["s"] + j / 100 - 0.05, w["s"] + (j + L) / 100 + 0.08
    os.remove(wav)
    ws = [w for w in ws if w["p"] > 0.25 or len(ws) > 6]  # einzelne Halluzinationen in stummen Clips
    cur = []
    for n_, w in enumerate(ws):
        cur.append(w)
        # Satzende auch ohne Punkt: nach einer langen Sprechpause (≥ 1,5 s) beginnt ein neuer Satz – sonst wird aus mehreren
        # Anläufen ein einziger Riesen-„Satz“, den die Planung nur ganz oder gar nicht nehmen kann.
        pause = n_ + 1 < len(ws) and ws[n_ + 1]["s"] - w["e"] >= 1.5
        if SENT_END.search(w["t"]) or pause:
            c["sents"].append(cur)
            cur = []
    if cur:
        c["sents"].append(cur)
    speech = sum(w["e"] - w["s"] for w in ws)
    c["talk"] = len(ws) >= 4 and speech >= 1.2

if not any(c.get("talk") for c in clips):  # niemand spricht → reine B-Roll: das macht musikschnitt.py (auf den Beat)
    log("Keine Sprache in den Clips → Musik-Schnitt")
    print(json.dumps({"musik": True}), flush=True)
    sys.exit(0)

# ─────────────────────────────── 3. Was ist zu sehen? (KI schaut Bilder an) ───────────────────────────────
stage("Schaut sich die Clips an …")


def frames(c):
    out = []
    for f in (0.2, 0.5, 0.8):
        p = os.path.join(A.out, f"f_{c['id']}_{f}.jpg")
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{c['dur'] * f:.2f}", "-i", c["file"], "-frames:v", "1", "-vf", "scale=384:-2", "-q:v", "5", p])
        out.append(base64.b64encode(open(p, "rb").read()).decode())
        os.remove(p)
    return out


def look(batch):
    parts = []
    for c in batch:
        parts.append({"text": f"Clip {c['id']} ({c['dur']:.1f} s, {'Hochformat' if c['h'] > c['w'] else 'Querformat'}), 3 Bilder:"})
        parts += [{"inline_data": {"mime_type": "image/jpeg", "data": b}} for b in frames(c)]
    res = gemini("Du bist Cutter für Kurzvideos eines Unternehmens (Restaurant, Laden, Dienstleister). Beschreibe jeden Clip kurz: "
                 "was zu sehen ist (Personen, Produkt, Laden, Schild/Preistafel, Essen …), Einstellung (Totale/Halbnah/Nah/Detail), "
                 "Qualität (gut / verwackelt / unscharf / zu dunkel / Fehlaufnahme) und ob er sich als B-Roll eignet (Bild ohne "
                 "Gesprächspartner, das man über gesprochenen Text legen kann).",
                 parts, {"type": "object", "properties": {"clips": {"type": "array", "items": {"type": "object", "properties": {
                     "id": {"type": "string"}, "inhalt": {"type": "string"}, "einstellung": {"type": "string"},
                     "qualitaet": {"type": "string"}, "broll": {"type": "boolean"}}, "required": ["id", "inhalt", "qualitaet", "broll"]}}},
                         "required": ["clips"]})
    return (res or {}).get("clips") or []


with ThreadPoolExecutor(4) as ex:
    for lst in ex.map(look, [clips[i:i + 6] for i in range(0, len(clips), 6)]):
        for d in lst:
            c = next((c for c in clips if c["id"] == d.get("id")), None)
            if c:
                c.update(inhalt=d.get("inhalt", ""), einstellung=d.get("einstellung", ""), qualitaet=d.get("qualitaet", ""),
                         broll_ok=bool(d.get("broll")))

# B-Roll-Material: Stellen OHNE Sprache (ganze stumme Clips, Pausen in Sprech-Clips ≥ 1,5 s – z. B. wenn jemand den Corndog
# in die Kamera hält). Je Stelle ein Bild aus der Mitte → die KI sagt, was zu sehen ist und ob es als Zwischenschnitt taugt.
def windows(c):
    ws = [w for st in c["sents"] for w in st]
    if not ws:
        return [(0.3, max(0.8, c["dur"] - 0.3))]
    out, prev = [], 0.0
    for w in ws + [{"s": c["dur"], "e": c["dur"]}]:
        if w["s"] - prev >= 1.5:
            out.append((prev + 0.2, w["s"] - 0.2))
        prev = max(prev, w["e"])
    return sorted(out, key=lambda x: x[0] - x[1])[:4]


mats = []
for c in clips:
    for k, (a, b) in enumerate(windows(c)):
        mats.append({"id": f"{c['id']}w{k + 1}", "c": c, "a": round(a, 2), "b": round(b, 2)})


def look_mats(batch):
    parts = []
    for m in batch:
        p = os.path.join(A.out, f"w_{m['id']}.jpg")
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{(m['a'] + m['b']) / 2:.2f}", "-i", m["c"]["file"], "-frames:v", "1",
             "-vf", "scale=320:-2", "-q:v", "5", p])
        parts += [{"text": f"Stelle {m['id']}:"}, {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(open(p, "rb").read()).decode()}}]
        os.remove(p)
    res = gemini("Beschreibe für jede Stelle in wenigen Wörtern, was zu sehen ist (z. B. „Nahaufnahme Cheeseburger in der Hand“, "
                 "„Preistafel“, „Laden von außen“, „Person redet“, „unscharf“). broll = taugt als kurzer Zwischenschnitt über "
                 "gesprochenem Text (Produkt, Essen, Zubereitung, Laden, Schild – gutes, ruhiges Bild). broll = false, sobald eine Person von vorn mit Gesicht zu sehen ist – auch wenn sie gerade schweigt, wartet oder zur Seite schaut.",
                 parts, {"type": "object", "properties": {"stellen": {"type": "array", "items": {"type": "object", "properties": {
                     "id": {"type": "string"}, "inhalt": {"type": "string"}, "broll": {"type": "boolean"}}, "required": ["id", "inhalt", "broll"]}}},
                         "required": ["stellen"]})
    return (res or {}).get("stellen") or []


with ThreadPoolExecutor(4) as ex:
    for lst in ex.map(look_mats, [mats[i:i + 10] for i in range(0, len(mats), 10)]):
        for d in lst:
            m = next((m for m in mats if m["id"] == d.get("id")), None)
            if m:
                m.update(inhalt=d.get("inhalt", ""), ok=bool(d.get("broll")))
mats = [m for m in mats if m.get("ok")]


def zeigt_gesicht(m):
    """Inhaber 08.10.: Über „Nummer 33“ lag als Zwischenbild eine Aufnahme, in der ER SELBST stumm am Tisch sitzt – man hört
    ihn reden und sieht ihn mit geschlossenem Mund. Ein Zwischenbild zeigt nie ein Gesicht von vorn (Produkt, Hände, Laden ja)."""
    try:
        import cv2
        kas = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        cap = cv2.VideoCapture(m["c"]["file"])
        n = 0
        for k in range(5):
            cap.set(cv2.CAP_PROP_POS_MSEC, 1000 * (m["a"] + (m["b"] - m["a"]) * (k + 0.5) / 5))
            ok, fr = cap.read()
            if not ok:
                continue
            g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
            g = cv2.resize(g, (640, int(640 * g.shape[0] / g.shape[1])))
            n += any(w >= 45 for (_, _, w, _) in kas.detectMultiScale(g, 1.1, 6, minSize=(45, 45)))
        cap.release()
        return n >= 2
    except Exception as e:
        log(f"  Gesichts-Prüfung Zwischenbild nicht möglich: {e}")
        return False


for m in list(mats):
    if m["c"].get("talk") and zeigt_gesicht(m):
        log(f"  Zwischenbild {m['id']} verworfen – Sprecher von vorn im Bild („{m.get('inhalt', '')[:40]}“)")
        mats.remove(m)
log(f"B-Roll-Material: {len(mats)} Stellen – " + "; ".join(f"{m['id']} {m.get('inhalt', '')[:30]}" for m in mats[:12]))

pics = []
if images:
    from PIL import Image, ImageOps
    for k, f in enumerate(images[:40]):
        try:
            im = ImageOps.exif_transpose(Image.open(f))
            im.thumbnail((900, 900))
            p = os.path.join(A.out, f"bild_{k + 1}.png")
            im.convert("RGBA").save(p)
            pics.append({"id": f"B{k + 1}", "file": p, "alpha": im.mode in ("RGBA", "LA", "P")})
        except Exception as e:  # noqa: BLE001  (HEIC ohne Plugin, kaputte Datei …)
            log("Bild übersprungen:", os.path.basename(f), str(e)[:60])
    if pics:
        parts = []
        for b in pics:
            sm = os.path.join(A.out, "b.jpg")
            Image.open(b["file"]).convert("RGB").resize((256, 256)).save(sm, quality=70)
            parts += [{"text": f"Bild {b['id']}:"}, {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(open(sm, "rb").read()).decode()}}]
            os.remove(sm)
        res = gemini("Beschreibe jedes Bild in wenigen Wörtern (was ist es, z. B. „Cheesecake Lotus freigestellt“, „Logo“, "
                     "„Preistafel“, „Foto Person“) und ordne es ein: produkt, logo, kommentar (Screenshot eines Kommentars), "
                     "foto oder grafik (Pfeil, Icon, Rahmen, Deko – nichts, was man als Sticker zeigen würde).", parts,
                     {"type": "object", "properties": {"bilder": {"type": "array", "items": {"type": "object", "properties": {
                         "id": {"type": "string"}, "inhalt": {"type": "string"},
                         "art": {"type": "string", "enum": ["produkt", "logo", "kommentar", "foto", "grafik"]}},
                         "required": ["id", "inhalt", "art"]}}}, "required": ["bilder"]})
        for d in (res or {}).get("bilder") or []:
            b = next((b for b in pics if b["id"] == d.get("id")), None)
            if b:
                b["inhalt"], b["art"] = d.get("inhalt", ""), d.get("art", "")
        # Rückmeldung 05.10.: der weiße Pfeil an seiner Hand „passt nicht“ – Deko-Grafiken (Pfeile, Icons) nie als Sticker
        for b in [b for b in pics if b.get("art") == "grafik"]:
            os.remove(b["file"])
            pics.remove(b)

talk = [c for c in clips if c.get("talk")]
log("Clips:", "; ".join(f"{c['id']} {c['dur']:.0f}s {'Sprache' if c.get('talk') else 'B-Roll'} {c.get('inhalt', '')[:40]}" for c in clips))
if not talk:
    sys.exit("In keinem Clip wird gesprochen.")

# ─────────────────────────────── 4. Schnittplan (KI) ───────────────────────────────
stage("Plant den Schnitt aus allen Clips …")
lines = []
for c in clips:
    head = f"### {c['id']} ({c['dur']:.1f} s) – Bild: {c.get('inhalt', '?')} | {c.get('einstellung', '')} | Qualität: {c.get('qualitaet', '?')}"
    lines.append(head + (" | B-Roll geeignet" if c.get("broll_ok") else ""))
    for k, st in enumerate(c["sents"]):
        lines.append(f"  {c['id']}.{k} [{st[0]['s']:.1f}–{st[-1]['e']:.1f}s] {' '.join(w['t'] for w in st)}")
for m in mats:
    lines.append(f"### B-Roll-Material {m['id']} ({m['c']['id']} {m['a']:.1f}–{m['b']:.1f} s): {m.get('inhalt', '?')}")
for b in pics:
    lines.append(f"### Bild {b['id']}: {b.get('inhalt', '?')}")
plans = gemini(
    "Du bist ein erfahrener Cutter für TikTok/Reels von Unternehmen. Der Kunde hat einen ganzen Ordner mit Rohclips (und evtl. "
    "Bildern) hochgeladen und will EIN fertiges, packendes Kurzvideo. Baue den Schnittplan:\n"
    "0) THEMEN ZUERST: Ordne jeden Clip einem Thema/Video zu (z. B. „Juniorbox-Zubereitung“ ≠ „bester Burger in Mannheim“ "
    "≠ „Angebote unter 10 €“ – andere Aussage, anderer Ort, andere Kleidung/Aufmachung = anderes Video). Eine Geschichte "
    "verwendet NUR Clips aus EINEM Thema (Feld „clips“). Clips eines anderen Themas nie anhängen.\n"
    "ARBEITSWEISE (so schneidet der Inhaber, 06.10.): Erst JEDEN Clip ansehen. Dann alle Clips eines Themas in der Reihenfolge "
    "in die Timeline legen, in der gedreht wurde bzw. wie es am meisten Sinn ergibt – die vielen kleinen Clips sind Puzzleteile "
    "EINES Videos. Ausgangspunkt ist also ALLES; erst danach wird gestrichen, und zwar nur mit Grund: Versprecher, Fehlversuche, "
    "doppelte Takes, Füllwörter, Leerlauf, Sätze an den Kameramann, Stellen, an denen die Kamera wegschwenkt, und was nicht zum "
    "Thema passt. Was keinen dieser Gründe hat, bleibt. Aus einem langen Clip (z. B. die ganze Zubereitung am Stück) also nicht "
    "nur ein paar Sätze herauspicken, sondern ihn durchgehend verwenden und nur das Schlechte herausnehmen.\n"
    "1) SPRECHTEILE = roter Faden: welche Sätze aus welchen Clips in welcher Reihenfolge (Satz-Nummern wie C3.2). Eine klare "
    "Geschichte: Aufhänger am Anfang, Hauptteil, Auflösung/Aufruf am Ende. Vorgespräche, Absprachen, Fehlversuche, doppelte "
    "Takes (nur den besten/letzten), lange Verabschiedungen, Nebensächliches weglassen (bei Umfragen/Gesprächen darf EIN kurzer "
    "Abschied – „Dankeschön!“ – „Tschüss!“ – als Schlusspunkt bleiben). Clips mit schlechter Qualität meiden. "
    + ("Gehören die Clips zu VERSCHIEDENEN Themen/Videos, nimm nur die stärkste zusammenhängende Geschichte. " if A.clips <= 1 else
       f"Gehören die Clips zu VERSCHIEDENEN Themen/Videos (z. B. „neue Sorte“ und „Angebote unter 10 €“), baue je Thema eine "
       f"EIGENE Geschichte – höchstens {A.clips}, die stärkste zuerst, kein Satz in zwei Geschichten. Ist alles EIN Thema, nur "
       f"eine Geschichte (nicht künstlich aufteilen). ") +
    "ENDE: Nach dem Abschluss-/Aufruf-Satz (z. B. „So sieht die Juniorbox aus … und dazu eine Capri-Sonne“) ist Schluss – "
    "danach nichts mehr. "
    "ZUBEREITUNG/ANLEITUNG: Schritte in der ECHTEN Reihenfolge (roh → braten/smashen → belegen → verpacken → fertig; die "
    "Clip-Nummern folgen meist der Drehreihenfolge) und VOLLSTÄNDIG – alle wesentlichen Schritte zeigen, auch Sätze, in "
    "denen beim Zubereiten erklärt wird. Lieber etwas länger und verständlich als lückenhaft. "
    "BILD UND WORT GEHÖREN ZUSAMMEN (Nutzer 06.10.): Ein Satz bleibt nur, wenn man dabei sieht, wovon gesprochen wird. Sagt "
    "jemand etwas über einen Schritt, der in diesem Moment NICHT im Bild ist („wenn das Brot fertig ist“, man sieht aber noch "
    "das Patty), dann lege das passende Material als Zwischenschnitt darüber; gibt es keins, lass nur DIESEN EINEN Satz weg. "
    "Der Arbeitsschritt selbst bleibt mit seinen übrigen Sätzen im Video – diese Regel streicht NIE einen ganzen Schritt der "
    "Zubereitung (Wenden, Käse, Brot, Soße, Belegen, Verpacken müssen alle vorkommen; Test 06.10.: 39 s ohne Käse, Soße und "
    "Belegen war falsch). Keine angefangenen oder abgebrochenen Sätze – ganz oder gar nicht. Kein SPRECH-Abschnitt, "
    "der nur kurz aufblitzt (unter ~1,5 s) und sofort wieder weg ist – gemeint sind nicht die gewollten Zwischenschnitte und "
    "Produktbilder aus Punkt 2 und 3, die bleiben ausdrücklich erwünscht (Test 06.10.: Cheesecake-Ordner kam ohne ein einziges "
    "Produktbild und nur mit einem statt zwei Videos heraus). Jede neue Einstellung beginnt mit der HANDLUNG (Hand greift "
    "in die Gurken, Patty wird vom Grill gekratzt), nicht mit dem Weg der Kamera dorthin. "
    "ANGEBOT/AUFRUF AM SCHLUSS (Inhaber 08.10.): Macht der Sprecher ein Angebot, eine Einladung oder eine Challenge („Mein "
    "Angebot an dich: Ich lade dich zum Essen ein … wenn du es ohne Milch schaffst, geht alles aufs Haus“), gehört es "
    "VOLLSTÄNDIG ans Ende des Videos: Ankündigung, Bedingung und Belohnung – jeweils die letzte saubere Fassung. Nie nur "
    "den letzten Satz davon ohne den Vorlauf (der wäre unverständlich) und nie ganz weglassen. "
    "Länge je nach Format "
    "(Umfrage mit einer Person 15–25 s, Vorstellung/Promo 20–40 s, Zubereitung/Anleitung 30–60 s, max. 60 s).\n"
    "2) ZWISCHENSCHNITTE (B-Roll) – wie ein Profi: Wird etwas genannt, das es als B-Roll-Material gibt, sieht man es GENAU in "
    "dem Moment (z. B. „Cheeseburger“ → Nahaufnahme Cheeseburger; „wir sind vom 1,99-Laden“ → Preistafel/Laden). Dann wieder "
    "die Person. Rhythmus: wenn passendes Material da ist, etwa alle 4–7 s ein Zwischenschnitt von 1–2,5 s; nie zwei direkt "
    "hintereinander, nie über Reaktionen/Pointen im Gesicht, nicht in den ersten 1,5 s. Ein Zwischenschnitt darf keinen "
    "späteren Schritt vorwegnehmen (nicht das fertig gebratene Patty zeigen, bevor es gesmasht wird). Gib das B-Roll-Material (z. B. C3w1), "
    "den Satz (ueber) und das Wort, bei dem es erscheinen soll.\n"
    "3) BILDER: Produktbilder/Sticker kurz einblenden, wenn genau dieses Produkt genannt wird (Satz + Wort). Nur wenn es "
    "wirklich passt.\n"
    "Wenn nichts passt, B-Roll/Bilder leer lassen.",
    [{"text": "\n".join(lines)}],
    {"type": "object", "properties": {"geschichten": {"type": "array", "items": {"type": "object", "properties": {
        "format": {"type": "string"}, "titel": {"type": "string"}, "ziel_s": {"type": "integer"},
        "clips": {"type": "array", "items": {"type": "string"}},
        "sprechteile": {"type": "array", "items": {"type": "string"}},
        "broll": {"type": "array", "items": {"type": "object", "properties": {
            "material": {"type": "string"}, "ueber": {"type": "string"}, "wort": {"type": "string"}, "dauer_s": {"type": "number"}},
            "required": ["material", "ueber", "wort"]}},
        "bilder": {"type": "array", "items": {"type": "object", "properties": {
            "bild": {"type": "string"}, "bei": {"type": "string"}, "wort": {"type": "string"}}, "required": ["bild", "bei", "wort"]}}},
     "required": ["format", "titel", "sprechteile", "broll", "bilder"]}}}, "required": ["geschichten"]}, timeout=150, deep=True)


ki.require_or_exit("Schnittplan")


def ref(s):
    m = re.fullmatch(r"\s*(C\d+)\.(\d+)\s*", s or "")
    if not m:
        return None
    c = next((c for c in clips if c["id"] == m.group(1)), None)
    k = int(m.group(2))
    return (c, k) if c and 0 <= k < len(c["sents"]) else None


def bild_zum_wort(kand):
    """Regel 111 (Inhaber 06.10.: „Wenn er sagt ‚zwei Gurken drauf‘, will ich sehen, wie zwei Gurken draufkommen. Kommt die
    Handlung erst in den Sekunden danach, schieb diese Sekunden vor über die Stimme.“) Die KI sieht je Stelle den Satz und
    Bilder mit Zeitangabe vom Satzanfang bis zum nächsten Satz und sagt, ab wann die angesagte Handlung wirklich zu sehen
    ist. Rückgabe {nr: {...}}; ohne KI oder im Zweifel bleibt alles, wie es ist."""
    parts = []
    for k in kand:
        step = max(0.75, (k["tb"] - k["ta"]) / 14)
        pat = os.path.join(A.out, f"bw_{k['nr']}_%02d.jpg")
        try:
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{k['ta']:.2f}", "-to", f"{k['tb']:.2f}", "-i", k["c"]["file"],
                 "-vf", f"fps=1/{step:.3f},scale=288:-2", "-q:v", "6", pat])
        except RuntimeError:
            continue
        imgs = sorted(f for f in os.listdir(A.out) if f.startswith(f"bw_{k['nr']}_"))
        if not imgs:
            continue
        parts.append({"text": f"Stelle {k['nr']} – der Sprecher sagt von Sekunde {k['ta']:.1f} bis {k['te']:.1f}: „{k['satz']}“. "
                              "Bilder ab Satzanfang:"})
        for j, f in enumerate(imgs):
            p = os.path.join(A.out, f)
            parts.append({"text": f"Sekunde {k['ta'] + (j + 0.5) * step:.1f}:"})
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(open(p, "rb").read()).decode()}})
            os.remove(p)
    if not parts:
        return {}
    res = gemini("Du bist Cutter für Kurzvideos. Ein Sprecher sagt an, was er tut („jetzt kommen zwei Gurken drauf“) – die Handlung "
                 "passiert im Rohmaterial oft erst ein paar Sekunden NACH dem Satz. Für jede Stelle bekommst du den Satz und Bilder "
                 "mit Zeitangabe vom Satzanfang bis einige Sekunden danach (er redet dort evtl. schon weiter – das ist egal). Bestimme je Stelle: "
                 "fehlstart = der Satz ist kein fertiger Satz fürs Video: abgebrochener Anlauf („Also, warte, weil ich will sagen …“), "
                 "Regie/Absprache mit dem Team („Sag mal …“, „Wie heißt das?“, „drei, zwei, eins“), Gestammel. "
                 "panne = der Satz spricht über ein Missgeschick, einen Fehler oder Unsicherheit beim Dreh („das Fleisch verbrannt“, "
                 "„wir holen es jetzt erst runter, das ist vielleicht …“, „warte, nochmal“) – kein Arbeitsschritt, gehört nie ins Video. "
                 "sprecher_im_bild = sieht man WÄHREND des Satzes das Gesicht des Sprechers (er spricht in die Kamera)? "
                 "sichtbar = ist die angesagte Handlung bzw. das genannte Ding in den Bildern eindeutig zu sehen? "
                 "von = Sekunde, in der die angesagte Handlung wirklich beginnt (NICHT das Hingreifen in einen Behälter, nicht der "
                 "Kameraschwenk dorthin – der Moment, in dem das Angesagte passiert: die Gurken landen auf dem Burger). "
                 "bis = Sekunde, in der die Handlung fertig ist (immer angeben; ist nichts zu sehen, von = bis = 0). was = was man dort sieht (höchstens 6 Wörter). "
                 "Nur angeben, was du wirklich siehst; im Zweifel sichtbar=false. Sagt der Satz keine Handlung an "
                 "(Begrüßung, Meinung, Preis), dann sichtbar=false.",
                 parts, {"type": "object", "properties": {"stellen": {"type": "array", "items": {"type": "object", "properties": {
                     "nr": {"type": "integer"}, "panne": {"type": "boolean"}, "fehlstart": {"type": "boolean"}, "sprecher_im_bild": {"type": "boolean"}, "sichtbar": {"type": "boolean"},
                     "von": {"type": "number"}, "bis": {"type": "number"}, "was": {"type": "string"}},
                     "required": ["nr", "panne", "fehlstart", "sprecher_im_bild", "sichtbar", "von", "bis"]}}}, "required": ["stellen"]}, timeout=180)
    return {int(d["nr"]): d for d in (res or {}).get("stellen") or [] if isinstance(d.get("nr"), (int, float))}


def alle_schritte(c):
    """Inhaber 07.10.: „Bei einer Zubereitung muss man den KOMPLETTEN Prozess sehen – es fehlten wieder Zwiebeln und Salatblatt.“
    Die Sätze allein reichen dafür nicht (nicht jeder Handgriff wird angesagt, manche Ansage ist genuschelt). Die KI sieht
    deshalb den ganzen Arbeits-Clip in Bildern mit Zeitangabe und listet JEDEN Arbeitsschritt mit der Sekunde, in der man
    ihn am besten sieht. Rückgabe [(sekunde, was)] in zeitlicher Reihenfolge, [] wenn die KI nichts liefert."""
    step = max(1.5, c["dur"] / 90)
    pat = os.path.join(A.out, f"as_{c['id']}_%03d.jpg")
    try:
        run(["ffmpeg", "-v", "error", "-y", "-i", c["file"], "-vf", f"fps=1/{step:.3f},scale=256:-2", "-q:v", "7", pat])
    except RuntimeError:
        return []
    imgs = sorted(f for f in os.listdir(A.out) if f.startswith(f"as_{c['id']}_"))
    parts = [{"text": f"Clip {c['id']} ({c['dur']:.0f} s). Bilder mit Zeitangabe:"}]
    for j, f in enumerate(imgs):
        p = os.path.join(A.out, f)
        parts.append({"text": f"Sekunde {(j + 0.5) * step:.1f}:"})
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(open(p, "rb").read()).decode()}})
        os.remove(p)
    res = gemini("Du bist Cutter für Kurzvideos. Die Bilder zeigen eine Zubereitung/einen Arbeitsablauf von Anfang bis Ende. "
                 "Liste ALLE Arbeitsschritte in zeitlicher Reihenfolge – der Zuschauer soll den kompletten Prozess sehen: jede Zutat, "
                 "die dazukommt (Fleisch auf die Platte, Käse, Soße, Gurken, Zwiebeln, Salat, Deckel …), jedes Wenden, Drücken, "
                 "Würzen, Zusammensetzen, Einpacken und das fertige Ergebnis. Je Schritt: sek = die Sekunde, in der man die Handlung "
                 "am deutlichsten sieht (die Zutat landet gerade), was = höchstens 5 Wörter. NICHT auflisten: Warten, Leerlauf, "
                 "Kameraschwenks, Hingreifen in Behälter, Missgeschicke (etwas fällt herunter, wird weggeworfen), denselben Schritt doppelt.",
                 parts, {"type": "object", "properties": {"schritte": {"type": "array", "items": {"type": "object", "properties": {
                     "sek": {"type": "number"}, "was": {"type": "string"}}, "required": ["sek", "was"]}}}, "required": ["schritte"]},
                 timeout=240, deep=True)
    out = sorted((float(d["sek"]), str(d.get("was") or "")) for d in (res or {}).get("schritte") or []
                 if isinstance(d.get("sek"), (int, float)) and 0 <= d["sek"] <= c["dur"])
    return out


def letzte_fassung(order):
    """Inhaber 08.10. (Halal-Ordner): „‚Mein Angebot an dich‘ bei Sekunde 14 war nur die Vorbereitung – er hat sich den Satz
    einmal selbst vorgesagt. Später im Clip kommt derselbe Satz noch einmal, in besser. Die Software muss aus mehreren
    Versuchen – auch wenn sie teils richtig sind – genau den richtigen Satz finden.“
    Wer einen Satz mehrfach aufnimmt, hört mit der guten Fassung auf. Deshalb: Kommt im selben Clip SPÄTER ein Satz, der die
    gewählten Sätze noch einmal enthält (und im Wesentlichen aus ihnen besteht – also kein Gespräch ÜBER den Satz), ersetzt
    er sie. Mehrere gewählte Teilsätze können dabei in einer späteren, zusammenhängenden Fassung aufgehen."""
    import difflib
    tok = lambda c, k: [re.sub(r"[^a-zäöüß0-9]", "", w["t"].lower()) for w in c["sents"][k] if re.sub(r"[^a-zäöüß0-9]", "", w["t"].lower())]

    def ohne_anlauf(B):  # „mein angebot an dich nein mein angebot an dich …“ → der Anlauf zählt nicht mit
        for n in (5, 4, 3):
            i = 0
            while i + 2 * n <= len(B):
                j = next((j for j in range(i + n, min(len(B) - n, i + n + 3) + 1) if B[j:j + n] == B[i:i + n]), None)
                if j is not None:
                    B = B[:i] + B[j:]
                else:
                    i += 1
        return B

    out = list(order)
    for c in {id(c): c for c, _ in order}.values():
        while True:
            ks = [k for cc, k in out if cc is c]
            best = None
            for j in range(len(c["sents"]) - 1, min(ks), -1):
                if j in ks:
                    continue
                B = ohne_anlauf(tok(c, j))
                if len(B) < 3:
                    continue
                drin, treffer = [], 0
                for k in ks:
                    A = tok(c, k)
                    if not A or k > j:
                        continue
                    m = sum(b.size for b in difflib.SequenceMatcher(None, A, B, autojunk=False).get_matching_blocks())
                    if m / len(A) >= 0.8:
                        drin.append(k)
                        treffer += m
                if drin and treffer / len(B) >= 0.55:
                    best = (j, drin)
                    break
            if not best:
                break
            j, drin = best
            i0 = next(i for i, (cc, k) in enumerate(out) if cc is c and k in drin)
            alt_txt = " / ".join(" ".join(w["t"] for w in c["sents"][k]) for k in drin)
            out = [x for x in out if not (x[0] is c and x[1] in drin)]
            out.insert(i0, (c, j))
            log(f"  Letzte Fassung: {c['id']}.{','.join(map(str, drin))} „{alt_txt[:60]}“ → {c['id']}.{j} „{' '.join(w['t'] for w in c['sents'][j])[:90]}“")
    return out


def build(plan, odir):
    """Eine Geschichte → montage.mp4 + plan.json in <odir>."""
    os.makedirs(odir, exist_ok=True)
    order = [r for r in (ref(x) for x in (plan or {}).get("sprechteile") or []) if r]
    if not order:  # Rückfall ohne KI: alle Sprech-Clips der Reihe nach
        order = [(c, k) for c in talk for k in range(len(c["sents"]))]
    order = letzte_fassung(order)
    # Die gute Fassung beginnt oft selbst mit einem Anlauf: „Mein Angebot an dich, nein, mein Angebot an dich, ich lade dich …“.
    # Im Test 08.10. wurde deshalb der GANZE Satz als Fehlstart gestrichen – der Schluss fehlte wieder. Der Anlauf samt
    # Korrekturwort wird hier abgeschnitten; der Satz beginnt beim zweiten Ansetzen.
    _kw = {"nein", "ne", "nee", "nochmal", "quatsch", "sorry", "stopp", "stop", "moment", "warte", "falsch", "anders"}
    _t = lambda w: re.sub(r"[^a-zäöüß0-9]", "", w["t"].lower())
    for c, k in order:
        st = c["sents"][k]
        tk = [_t(w) for w in st]
        for n in (5, 4, 3, 2):
            hit = next((j for j in range(n + 1, min(len(tk) - n, n + 3) + 1)
                        if tk[j:j + n] == tk[:n] and all(tk[:n]) and any(x in _kw for x in tk[n:j])), None)
            if hit:
                log(f"  Anlauf gekappt: {c['id']}.{k} „{' '.join(w['t'] for w in st[:hit])}“ | {' '.join(w['t'] for w in st[hit:])[:70]}")
                c["sents"][k] = st[hit:]
                break
    # Inhaber 06.10. abends („du hast viel zu viel rausgenommen – Patty drauflegen, Soße auf den Deckel, Salat fehlen“):
    # In einer Zubereitung/Anleitung lässt die Montage zwischen dem ersten und letzten gewählten Satz eines Clips nichts aus.
    # Versprecher, Fehlstarts und Füllwörter entfernt danach die Pipeline, Pannen-Sätze der Schritt „Bild folgt dem Wort“.
    if re.search(r"zubereit|anleitung|tutorial|rezept|so wird|so entsteht|behind", str((plan or {}).get("format") or "").lower()):
        voll, dazu = [], 0
        for c, k in order:
            if voll and voll[-1][0] is c and k > voll[-1][1] + 1:
                dazu += k - voll[-1][1] - 1
                voll += [(c, j) for j in range(voll[-1][1] + 1, k)]
            if (c, k) not in [(a_, b_) for a_, b_ in voll if a_ is c]:
                voll.append((c, k))
        # Kompletter Prozess (Inhaber 07.10.: „man muss den ganzen Prozess sehen, von Anfang bis Ende“): Ein Arbeits-Clip, aus dem
        # mindestens 3 Sätze gewählt sind, kommt mit ALLEN seinen Sätzen ins Video – auch denen vor dem ersten und nach dem
        # letzten gewählten. Pannen streicht der Schritt „Bild folgt dem Wort“, Fehlstarts/Füllwörter die Pipeline.
        for c in {id(c): c for c, _ in voll}.values():
            ks = [k for cc, k in voll if cc is c]
            if len(ks) >= 3 and len(ks) < len(c["sents"]):
                i0 = next(i for i, (cc, _) in enumerate(voll) if cc is c)
                dazu += len(c["sents"]) - len(ks)
                voll = [x for x in voll if x[0] is not c]
                voll[i0:i0] = [(c, k) for k in range(len(c["sents"]))]
        # Inhaber 07.10. („die Reihenfolge stimmt nicht – erst alles ansehen, in die richtige Reihenfolge stellen, DANN schneiden“):
        # Eine Zubereitung läuft in Dreh-Reihenfolge. Begrüßung (erster Block) und Schluss (letzter Block) bleiben, wo die KI
        # sie hingestellt hat – alles dazwischen wird nach Clip (Aufnahme-Reihenfolge) und Satz sortiert.
        nr = {id(c): i for i, c in enumerate(clips)}
        if len({id(c) for c, _ in voll}) >= 3:
            a_end = next((i for i, (c, _) in enumerate(voll) if c is not voll[0][0]), len(voll))
            b_start = len(voll) - next((i for i, (c, _) in enumerate(reversed(voll)) if c is not voll[-1][0]), len(voll))
            mitte = sorted(voll[a_end:b_start], key=lambda x: (nr[id(x[0])], x[1]))
            if mitte != voll[a_end:b_start]:
                log("Zubereitung: Sätze in Dreh-Reihenfolge gebracht")
            voll = voll[:a_end] + mitte + voll[b_start:]
        if dazu:
            log(f"Zubereitung: {dazu} ausgelassene Sätze zwischen den gewählten wieder aufgenommen (kein Arbeitsschritt fehlt)")
        order = voll
    log(f"Plan: {(plan or {}).get('format')} – {len(order)} Sätze, {len((plan or {}).get('broll') or [])} B-Rolls, "
        f"{len((plan or {}).get('bilder') or [])} Bilder")
    # Regel 103: prüfbar machen, ob aus den Puzzleteilen wirklich EIN ganzes Video wird – je Sprech-Clip des Themas: wie viele
    # seiner Sätze sind im Plan? (KI-Noten sagen dazu nichts: am 06.10. bekam ein Video ohne Käse/Soße/Belegen Note 9.)
    thema = set()
    for x in (plan or {}).get("clips") or []:  # die KI nennt IDs („C4“); zur Sicherheit auch „Clip C4“ oder Objekte verstehen
        m_id = re.search(r"C\d+", str(x.get("id") if isinstance(x, dict) else x) or "")
        if m_id:
            thema.add(m_id.group(0))
    if not any(c["id"] in thema for c in talk):  # nichts erkannt → wenigstens die Clips messen, aus denen Sätze kommen
        thema = {c["id"] for c, _ in order}
        log("  Abdeckung: Themen-Zuordnung der KI nicht lesbar – gemessen werden nur die verwendeten Clips")
    for c in talk:
        if c["id"] not in thema or not c.get("sents"):
            continue
        n_used = len({k for cc, k in order if cc is c})
        anteil = n_used / len(c["sents"])
        log(f"  Abdeckung {c['id']} ({c['dur']:.0f} s): {n_used} von {len(c['sents'])} Sätzen ({anteil:.0%})"
            + ("  ← unter der Hälfte: prüfen, ob Arbeitsschritte fehlen (bei vielen Versprechern kann das richtig sein)" if c["dur"] >= 20 and anteil < 0.5 else "")
            + ("  ← Clip mit Sprache gar nicht verwendet" if n_used == 0 else ""))

    # ─────────────────────────────── 5. Sprech-Stellen zu EINER Datei zusammensetzen ───────────────────────────────
    stage("Setzt die Clips zusammen …")
    land = sum(1 for c in talk if c["w"] >= c["h"]) >= len(talk) / 2
    TW, TH = (1920, 1080) if land else (1080, 1920)
    FPS = min(60.0, max(25.0, sorted(c["fps"] for c in talk)[len(talk) // 2]))
    pieces = []  # zusammenhängende Satz-Folgen aus demselben Clip
    for c, k in order:
        if pieces and pieces[-1]["c"] is c and k == pieces[-1]["k1"] + 1:
            pieces[-1]["k1"] = k
        else:
            pieces.append({"c": c, "k0": k, "k1": k})
    files_c, t, anchors, joins, wanchors, zeigen = [], 0.0, {}, [], {}, []
    low = lambda x: re.sub(r"[^\wäöüß]", "", (x or "").lower())


    def at_word(ref_s, wort):
        """Zeit (zusammengesetzte Datei) des genannten Wortes im Satz – sonst Satzanfang."""
        ref_s = (ref_s or "").strip()
        for t_w, tt in wanchors.get(ref_s, []):
            if low(wort) and (low(wort) in low(t_w) or low(t_w) in low(wort) and len(low(t_w)) >= 4):
                return tt
        return anchors.get(ref_s)
    # Stücke an langen Pausen (> 0,9 s) innerhalb der Sätze teilen: dort steckt oft Geflüster vom Team („sag jetzt: …“), das
    # Whisper nicht mitschreibt – die Pipeline hielte es sonst für Sprache und ließe es drin (Malo's-Test 03.10.).
    subs = []
    for p in pieces:
        ws = [w for k in range(p["k0"], p["k1"] + 1) for w in p["c"]["sents"][k]]
        cur = [ws[0]]
        for a, b in zip(ws, ws[1:]):
            if b["s"] - a["e"] > 0.9:
                subs.append({"c": p["c"], "ws": cur})
                cur = []
            cur.append(b)
        subs.append({"c": p["c"], "ws": cur})
    allw = {id(c): [w for st in c["sents"] for w in st] for c in clips}
    first_of = {id(st[0]): f"{c['id']}.{k}" for c in clips for k, st in enumerate(c["sents"])}
    sent_of = {id(w): f"{c['id']}.{k}" for c in clips for k, st in enumerate(c["sents"]) for w in st}
    # ── Regel 111: Das Bild folgt dem Wort ──
    # Einheiten = Satz für Satz (auch mitten in einem Stück). Die KI sieht je Satz die Bilder der nächsten Sekunden und sagt,
    # wann die angesagte Handlung wirklich zu sehen ist. Der TON bleibt, wo er ist – das BILD wird aus der Handlung genommen,
    # auch wenn er dort schon weiterredet (man sieht ja nur Hände/Essen). Spricht jemand in die Kamera, bleibt alles synchron.
    units = []
    for p in subs:
        c, ws = p["c"], p["ws"]
        cw = allw[id(c)]
        grp = []
        for w in ws:
            if grp and sent_of[id(w)] == sent_of[id(grp[-1][-1])]:
                grp[-1].append(w)
            else:
                grp.append([w])
        for gi, g in enumerate(grp):
            i0, i1 = next(i for i, w in enumerate(cw) if w is g[0]), next(i for i, w in enumerate(cw) if w is g[-1])
            prev_e = cw[i0 - 1]["e"] if i0 else 0.0
            next_s = cw[i1 + 1]["s"] if i1 + 1 < len(cw) else c["dur"]
            s0 = (grp[gi - 1][-1]["e"] + g[0]["s"]) / 2 if gi else max(0.0, prev_e + 0.05, g[0]["s"] - 0.35)
            s1 = (g[-1]["e"] + grp[gi + 1][0]["s"]) / 2 if gi + 1 < len(grp) else max(g[-1]["e"] + 0.05, min(c["dur"], next_s - 0.05, g[-1]["e"] + 0.6))
            units.append({"c": c, "ws": g, "s0": s0, "s1": s1, "innen": gi > 0, "satz": " ".join(w["t"] for w in g)})
    if units:
        units[-1]["s1"] = max(units[-1]["s1"], min(units[-1]["c"]["dur"], units[-1]["ws"][-1]["e"] + 1.3))  # Videoende klingt aus
    for n, u in enumerate(units):
        u.update(nr=n, ta=u["ws"][0]["s"], te=u["ws"][-1]["e"], tb=min(u["c"]["dur"], u["ws"][-1]["e"] + 8.0))
    bw = {}
    kand = [u for u in units if len(u["ws"]) >= 2 and u["tb"] - u["ta"] >= 1.5]
    if kand:
        stage("Sucht zu jedem Satz das passende Bild …")
        try:
            for i in range(0, len(kand), 6):   # nacheinander – parallele Bild-Anfragen sprengen das Gratis-Limit pro Minute
                bw.update(bild_zum_wort(kand[i:i + 6]))
            offen = [u for u in kand if u["nr"] not in bw]
            if offen:  # Antwort abgeschnitten/ausgefallen (06.10.: ein ganzer Block fehlte) → in kleinen Portionen noch einmal
                log(f"Bild folgt dem Wort: {len(offen)} Sätze ohne Antwort – zweiter Versuch in kleinen Portionen")
                for i in range(0, len(offen), 3):
                    bw.update(bild_zum_wort(offen[i:i + 3]))
                offen = [u for u in kand if u["nr"] not in bw]
                if offen:
                    log(f"Bild folgt dem Wort: {len(offen)} Sätze weiter ohne Antwort – bleiben, wie gedreht")
        except Exception as e:  # noqa: BLE001
            log(f"Bild folgt dem Wort: KI-Schritt ausgefallen ({e}) – Bild bleibt, wie gedreht")
    # Regel 106: Pannen-Sätze („das Fleisch verbrannt“) kommen nie ins Video – die KI markiert sie hier gleich mit
    # Feste Sicherung zusätzlich zur KI (Testreihe 07.10.: „wir holen es jetzt erst runter, das ist vielleicht …“ blieb drin, weil
    # die KI nur den Satz davor als Panne markiert hatte): eindeutige Pannen-Wörter, und der Satz direkt NACH einer Panne im
    # selben Clip, wenn er sie weiter bespricht.
    fehl_re = re.compile(r"^\W*(also,? )?warte\b|wie hei(ß|ss)t das|^\W*sag mal\b|drei, zwei, eins|ich will sagen|^\W*also,? als er\W*$", re.I)
    for u in units:
        d = bw.get(u["nr"]) or {}
        # Ein langer, vollständiger Satz (≥ 8 Wörter) ist kein Fehlstart, nur weil die KI darin ein Stolpern sieht
        # … und ein TEILSTÜCK eines Satzes auch nicht (Test 08.10.: aus „Halal Thai | ist kein | Trend, das gibt es …“ strich die
        # KI in jedem Lauf das Mittelstück „ist kein“ – übrig blieb einmal „Thai ist kein Trend“, einmal nur „Trend“). Anläufe
        # INNERHALB eines Satzes erkennt der Feinschnitt am Wortlaut (Regel 116/118); hier fällt nur ein ganzer Satz weg.
        teil = sum(1 for v in units if sent_of[id(v["ws"][0])] == sent_of[id(u["ws"][0])]) > 1
        if ((d.get("fehlstart") and len(u["ws"]) < 8 and not teil) or fehl_re.search(u["satz"])) and not re.search(r"warten (kurz|bis)", u["satz"], re.I):
            u["raus"] = True
            log(f"  Fehlstart gestrichen: {sent_of[id(u['ws'][0])]} „{u['satz'][:60]}“")
    pan_re = re.compile(r"verbrannt|angebrannt|ist falsch|falsch gemacht|oh no|schiefgegangen|\bah, ah\b", re.I)
    nach_re = re.compile(r"vielleicht|holen es|erst runter|nochmal neu|von vorne", re.I)
    vor = None
    for u in units:
        d = bw.setdefault(u["nr"], {}) if (pan_re.search(u["satz"]) or (vor is not None and vor["c"] is u["c"] and vor.get("_p") and nach_re.search(u["satz"]))) else (bw.get(u["nr"]) or {})
        if pan_re.search(u["satz"]) or (vor is not None and vor["c"] is u["c"] and vor.get("_p") and nach_re.search(u["satz"])):
            d["panne"] = True
        u["_p"] = bool(d.get("panne"))
        vor = u
        if d.get("panne"):
            u["raus"] = True
            log(f"  Panne gestrichen: {sent_of[id(u['ws'][0])]} „{u['satz'][:60]}“")
    units = [u for u in units if not u.get("raus")]
    # ── Regel 114: Zubereitung wie der Inhaber sie selbst schneidet (sein fertiger Junior-Burger-Schnitt, 28 s): Begrüßung
    # gesprochen, dann die Arbeitsschritte als SCHNELLE BILDFOLGE ohne Erzählung (je Schritt gut 1 s, Musik), dann wieder
    # gesprochen („jetzt ist der Burger fertig“, Verpackung, Schluss). Jeder angesagte Schritt, dessen Handlung die KI im
    # Bild gefunden hat, wird zu einem stummen Zeige-Stück genau an der Handlung; Erklär-Sätze ohne eigene Handlung entfallen
    # dort. Sätze mit Sprecher im Bild bleiben gesprochen. Ohne Auskunft der KI bleibt der Satz, wie er ist.
    schnell = bool(re.search(r"zubereit|anleitung|tutorial|rezept|so wird|so entsteht|behind", str((plan or {}).get("format") or "").lower()))
    n_zeig = sum(1 for u in units if (bw.get(u["nr"]) or {}).get("sichtbar") and not (bw.get(u["nr"]) or {}).get("sprecher_im_bild"))
    # Test 07.10. (ein einzelner langer Küchen-Clip): Dort gibt es keine Begrüßung/keinen Schluss vor der Kamera – als reine
    # Bildfolge bliebe kein einziger gesprochener Satz. Dann bleibt die Erzählung, und das Bild wird Satz für Satz an die
    # Handlung geschoben (Regel 111). Die schnelle Bildfolge gibt es nur, wenn drumherum jemand vor der Kamera spricht.
    n_kamera = sum(1 for u in units if (bw.get(u["nr"]) or {}).get("sprecher_im_bild"))
    # Entscheidung des Inhabers 07.10.: Ein Ordner mit vielen kleinen Clips wird geschnitten WIE EIN LANGES VIDEO – seine Stimme
    # bleibt, das Bild folgt dem Wort (Regel 111), der komplette Prozess ist zu sehen („bei einem langen Video klappt das schon
    # ganz gut“). Die stumme schnelle Bildfolge (Regel 114) ist deshalb nicht mehr der Normalfall; sie bleibt als Schalter
    # VELOX_BILDFOLGE=1 erhalten.
    # Entscheidung des Inhabers 08.10. nachts, nach dem Malo's-Test mit Erzählung (82 s, Satzfetzen beim Arbeiten):
    # „Mach wie mein Schnitt.“ → Die schnelle stumme Bildfolge ist wieder der NORMALFALL für Zubereitungen, sobald drumherum
    # jemand vor der Kamera spricht (Begrüßung/Schluss). Abschalten nur noch mit VELOX_BILDFOLGE=0.
    BILDFOLGE = os.environ.get("VELOX_BILDFOLGE", "1") != "0"
    if schnell and n_zeig >= 3 and not (n_kamera >= 2 and BILDFOLGE):
        log("Zubereitung: Erzählung bleibt, das Bild folgt dem Wort (wie bei einem langen Video)")
    if schnell and n_zeig >= 3 and n_kamera >= 2 and BILDFOLGE:
        neu_u, frei_z, weg = [], {}, 0
        # Bildfolge nur in den Clips, in denen wirklich gearbeitet wird (mind. 3 gezeigte Schritte). Ein einzelner Satz aus dem
        # Off in einem anderen Clip („Und jetzt ist unser Junior Burger fertig“) bleibt gesprochen – so auch im Schnitt des Inhabers.
        zahl = {}
        for u in units:
            d = bw.get(u["nr"]) or {}
            if d.get("sichtbar") and not d.get("sprecher_im_bild"):
                zahl[id(u["c"])] = zahl.get(id(u["c"]), 0) + 1
        kueche = {k for k, v in zahl.items() if v >= 3}
        for u in units:
            d, c = bw.get(u["nr"]) or {}, u["c"]
            if not d or d.get("sprecher_im_bild") or id(c) not in kueche:
                neu_u.append(u)
                continue
            if not d.get("sichtbar") or d.get("von") is None:
                weg += 1
                continue
            von = min(max(float(d["von"]), u["ta"] - 0.5), c["dur"] - 1.0)
            bis = max(float(d.get("bis") or von + 1.2), von + 0.6)
            a = max(von - 0.15, frei_z.get(id(c), 0.0))
            b = min(c["dur"] - 0.05, a + min(1.5, max(1.0, bis - von + 0.3)))
            if b - a < 0.7:
                weg += 1
                continue
            frei_z[id(c)] = b
            neu_u.append({**u, "zeig": (a, b), "s0": a, "s1": b})
            log(f"  Zeige-Stück {sent_of[id(u['ws'][0])]} „{u['satz'][:40]}“ → {a:.1f}–{b:.1f} s ({str(d.get('was') or '')[:50]})")
        # Kompletter Prozess (Inhaber 07.10.): je Arbeits-Clip alle sichtbaren Schritte von der KI holen und Lücken füllen
        for c in [c for c in clips if id(c) in kueche]:
            try:
                alle = alle_schritte(c)
            except Exception as e:  # noqa: BLE001
                alle = []
                log(f"  Alle Schritte {c['id']}: KI-Schritt ausgefallen ({e})")
            if len(alle) < 3:
                continue
            da = [u for u in neu_u if u.get("zeig") and u["c"] is c]
            pos = next((i for i, u in enumerate(neu_u) if u.get("zeig") and u["c"] is c), None)
            if pos is None or not allw[id(c)]:
                continue
            stuecke = [u["zeig"] for u in da]
            neu_n = 0
            for sek, was in alle:
                if any(a - 2.0 <= sek <= b + 2.0 for a, b in stuecke):
                    continue          # diesen Schritt zeigt schon ein (genauer getroffenes) Stück aus den Sätzen
                a = max(0.0, sek - 0.5)
                stuecke.append((a, min(c["dur"] - 0.05, a + 1.3)))
                neu_n += 1
                log(f"  Zusätzlicher Schritt {c['id']} {a:.1f} s: {was[:50]}")
            stuecke = sorted(x for x in stuecke if x[1] - x[0] >= 0.7)
            sauber = []
            for a, b in stuecke:              # nie dasselbe Bild zweimal
                a = max(a, sauber[-1][1]) if sauber else a
                if b - a >= 0.7:
                    sauber.append((a, b))
            # Test Malo's 08.10. nachts: 25 Schritte × 1,3 s = 32 s Bildfolge – das ganze Video des Inhabers hat 28 s. Die
            # Bildfolge bekommt ein Zeitbudget von 14 s: je Schritt 14 s / Anzahl, mindestens 0,55 s, höchstens 1,2 s, immer
            # um den Moment der Handlung herum (alle Schritte bleiben zu sehen, nur kürzer).
            je = min(1.2, max(0.55, 14.0 / max(1, len(sauber))))
            eng = []
            for a, b in sauber:
                m_ = (a + b) / 2
                a2 = max(a, m_ - je / 2)
                eng.append((a2, min(b, a2 + je)))
            sauber = eng
            w0 = allw[id(c)][0]
            neue = [{"c": c, "ws": [w0], "s0": a, "s1": b, "zeig": (a, b), "innen": False, "satz": "", "nr": -1} for a, b in sauber]
            neu_u = [u for u in neu_u[:pos] if not (u.get("zeig") and u["c"] is c)] + neue + \
                    [u for u in neu_u[pos:] if not (u.get("zeig") and u["c"] is c)]
            log(f"Kompletter Prozess {c['id']}: {len(sauber)} Schritte in der Bildfolge ({neu_n} davon zusätzlich aus dem Bild erkannt)")
        log(f"Zubereitung als schnelle Bildfolge: {sum(1 for u in neu_u if u.get('zeig'))} Arbeitsschritte gezeigt, "
            f"{weg} Erklär-Sätze ohne eigene Handlung entfallen, {sum(1 for u in neu_u if not u.get('zeig'))} Sätze gesprochen")
        units = neu_u
        # Der gesprochene Teil NACH der Bildfolge (Test Malo's: 27 s Einpacken Satz für Satz – beim Inhaber sind es vier Sätze:
        # „Jetzt ist unser Junior Burger fertig. Spielzeug ist immer anders. So sieht die Junior Box bei Malo's aus. Und noch
        # dazu eine Capri-Sonne.“). Ist er länger als 13 s, wählt die KI daraus: Fertig-Meldung, höchstens eine Besonderheit, Schluss.
        letzt = max((i for i, u in enumerate(units) if u.get("zeig")), default=None)
        nach = [u for u in units[letzt + 1:] if not u.get("zeig")] if letzt is not None else []
        if sum(u["te"] - u["ta"] for u in nach) > 13.0 and len(nach) > 4:
            wahl = None
            try:
                r3 = gemini(
                    "Du bist Cutter für Kurzvideos. Ein Zubereitungs-Video: Begrüßung, dann die Zubereitung als schnelle Bildfolge "
                    "mit Musik, dann spricht die Person wieder. Unten stehen die Sätze dieses Schlussteils mit Dauer. Er ist zu "
                    "lang. Wähle höchstens 12 Sekunden aus, in dieser Reihenfolge: (1) die Fertig-Meldung („Jetzt ist unser Burger "
                    "fertig“), (2) höchstens EINE Besonderheit, die neugierig macht (z. B. „Das Spielzeug ist immer anders“), "
                    "(3) den Schluss – die letzten ein bis zwei vollständigen Sätze („So sieht die Junior Box bei uns aus. Und noch "
                    "dazu eine Capri-Sonne.“). Nur vollständige, verständliche Sätze; keine Arbeitsanweisungen Schritt für Schritt "
                    "(„jetzt in die Tüte, Pommes daneben“), keine Satzfetzen, keine Doppelungen. Gib die Nummern zurück.",
                    [{"text": "\n".join(f"{i}: [{u['te'] - u['ta']:.1f} s] {u['satz']}" for i, u in enumerate(nach))}],
                    {"type": "object", "properties": {"nummern": {"type": "array", "items": {"type": "integer"}}}, "required": ["nummern"]},
                    timeout=90, deep=True)
                wahl = sorted({int(i) for i in (r3 or {}).get("nummern") or [] if 0 <= int(i) < len(nach)})
            except Exception as e:  # noqa: BLE001
                log(f"  Schlussteil kürzen: KI-Schritt ausgefallen ({e})")
            if not wahl or sum(nach[i]["te"] - nach[i]["ta"] for i in wahl) > 16.0:
                fertig = next((i for i, u in enumerate(nach) if re.search(r"fertig|ready", u["satz"], re.I)), 0)
                wahl = sorted({fertig, len(nach) - 2, len(nach) - 1})
            raus = [u for i, u in enumerate(nach) if i not in wahl]
            units = [u for u in units if not any(u is x for x in raus)]
            log(f"Schlussteil nach der Bildfolge gekürzt: {len(nach)} → {len(wahl)} Sätze "
                f"({sum(nach[i]['te'] - nach[i]['ta'] for i in wahl):.0f} s) – " + " | ".join(nach[i]["satz"][:40] for i in wahl))
    n_shift, frei = 0, {}
    for k, u in enumerate(units):
        if u.get("zeig"):
            continue
        d, c, L = bw.get(u["nr"]) or {}, u["c"], u["s1"] - u["s0"]
        kurz = f"{sent_of[id(u['ws'][0])]} „{u['satz'][:44]}“"
        fest = bool(d.get("sprecher_im_bild")) or not d   # ohne Auskunft der KI: nichts verschieben
        # Grenze: das Bild darf nie in einen späteren Abschnitt desselben Clips laufen, der lippensynchron bleiben muss
        grenze = min([x["s0"] for x in units[k + 1:] if x["c"] is c and ((bw.get(x["nr"]) or {}).get("sprecher_im_bild") or not bw.get(x["nr"]))]
                     + [c["dur"] - 0.05])
        # Fehlersuche 07.10. (Inhaber: „Wort und Bild passen nicht, es muss 1:1 sein; nach der Ansage sieht man die Handlung
        # nicht, sofort Cut“). Drei Denkfehler der alten Fassung: (1) Sätze OHNE eigene Handlung bekamen das Bild „danach“ und
        # verbrauchten so die Handlung des nächsten Satzes – alles verrutschte um bis zu 10 s. (2) Fehlte die Angabe „bis“,
        # wurde die Handlung nach dem Satz nie weitergezeigt. (3) Fehlstarts bekamen Handlungsbilder. Jetzt:
        #   – Satz MIT sichtbarer Handlung: Bild beginnt kurz vor der Handlung, und sie läuft danach stumm weiter, bis sie fertig ist.
        #   – Satz OHNE eigene Handlung: Bild bleibt an seiner eigenen Stelle (nur wenn die schon gezeigt wurde, läuft es weiter).
        v0, hat = u["s0"], False
        if not fest:
            f0 = frei.get(id(c), 0.0)
            if d.get("sichtbar") and float(d.get("bis") or 0) > 0:
                von = min(max(float(d.get("von") or 0), 0.0), c["dur"] - 0.5)
                bis = max(float(d["bis"]), von + 1.0)
                v0, hat = max(0.0, von - 0.4), True
                if v0 < f0:                               # ein Teil davon war schon zu sehen (zwei Sätze, dieselbe Handlung)
                    v0, hat = f0, f0 < bis - 0.6          # nie dasselbe Bild zweimal, nie zurückspringen → dort weiter
            elif u["s0"] < f0:
                v0 = f0                                   # eigene Stelle schon gezeigt → Bild läuft einfach weiter
            v0 = max(0.0, min(v0, grenze - L))
        shift = v0 - u["s0"]
        if abs(shift) < 0.5:
            shift, v0 = 0.0, u["s0"]
        u["shift"] = shift
        ende = v0 + L
        if hat:  # die Handlung zu Ende zeigen (stumm, mit Musik) – höchstens 3 s über den Satz hinaus, nie in die nächste Handlung
            nxt = next((x for x in units[k + 1:] if x["c"] is c), None)
            nd = (bw.get(nxt["nr"]) or {}) if nxt else {}
            halt = min(bis + 0.4, ende + 3.0, grenze,
                       (float(nd["von"]) - 0.4) if nd.get("sichtbar") and float(nd.get("bis") or 0) > 0 and float(nd["von"]) - 0.4 > ende else 1e9)
            if ende - v0 < 1.6:                           # sehr kurzer Satz: die Handlung mindestens 1,6 s sehen
                halt = max(halt, min(v0 + 1.6, grenze))
            if halt - ende >= 0.4:
                u["extra"] = (ende, halt)
                ende = halt
        if not fest and not hat:  # Satz ohne eigene Handlung darf die Handlung des NÄCHSTEN Satzes nicht verbrauchen
            nv = next(((bw.get(x["nr"]) or {}) for x in units[k + 1:] if x["c"] is c and (bw.get(x["nr"]) or {}).get("sichtbar")
                       and float((bw.get(x["nr"]) or {}).get("bis") or 0) > 0), None)
            if nv and v0 < float(nv["von"]) - 0.4 < ende:
                ende = float(nv["von"]) - 0.4
        frei[id(c)] = max(frei.get(id(c), 0.0), ende)
        if fest and d:
            log(f"  Bild↔Wort {kurz}: Sprecher im Bild – bleibt lippensynchron")
        elif d and not d.get("sichtbar") and shift:
            log(f"  Bild↔Wort {kurz}: keine eigene Handlung – eigene Stelle schon gezeigt, Bild läuft weiter ({shift:+.1f} s)")
        elif d and d.get("sichtbar"):
            n_shift += shift > 0
            log(f"  Bild↔Wort {kurz}: Handlung {float(d.get('von') or 0):.1f}–{float(d.get('bis') or 0):.1f} s ({str(d.get('was') or '')[:44]}) → "
                + (f"Bild um {shift:+.1f} s verschoben" if shift else "passt schon")
                + (f", danach {u['extra'][1] - u['extra'][0]:.1f} s Handlung weiter" if u.get("extra") else ""))
    if kand:
        log(f"Bild folgt dem Wort: {len(kand)} Sätze geprüft, {n_shift} mal das Bild vorgezogen")
    # Nachbarn ohne Verschiebung aus demselben Stück wieder verbinden (kein unnötiger Schnitt mitten im Redefluss)
    subs = []
    for u in units:
        v = subs[-1] if subs else None
        if v and u["innen"] and not u.get("zeig") and not v.get("zeig") and v["c"] is u["c"] and not v.get("shift") and not u.get("shift") and not v.get("extra") and abs(v["s1"] - u["s0"]) < 0.01:
            v["ws"] = v["ws"] + u["ws"]
            v["s1"] = u["s1"]
            v["extra"] = u.get("extra")
        else:
            subs.append({"c": u["c"], "ws": list(u["ws"]), "s0": u["s0"], "s1": u["s1"], "shift": u.get("shift"), "extra": u.get("extra"),
                         "zeig": u.get("zeig")})
    for n, p in enumerate(subs):
        c, ws = p["c"], p["ws"]
        cw = allw[id(c)]
        i0, i1 = next(i for i, w in enumerate(cw) if w is ws[0]), next(i for i, w in enumerate(cw) if w is ws[-1])
        prev_e = cw[i0 - 1]["e"] if i0 else 0.0
        next_s = cw[i1 + 1]["s"] if i1 + 1 < len(cw) else c["dur"]
        # Luft bis kurz vor das Nachbarwort (Whisper setzt Wortenden oft zu früh – „Capri-Sonne“ war sonst abgeschnitten);
        # am Videoende besonders großzügig. Stille entfernt danach ohnehin die Pipeline.
        s0 = max(0.0, prev_e + 0.05, ws[0]["s"] - 0.35)
        s1 = min(c["dur"], next_s - 0.05, ws[-1]["e"] + (1.3 if n == len(subs) - 1 else 0.6))
        s1 = max(s1, ws[-1]["e"] + 0.05)
        s0, s1 = p.get("s0", s0), p.get("s1", s1)  # Regel 111: die Stücke bringen ihre Grenzen mit
        out = os.path.join(odir, f"teil_{n:02d}.mp4")
        vf = f"scale={TW}:{TH}:force_original_aspect_ratio=increase,crop={TW}:{TH},fps={FPS:.3f},setsar=1"
        enc = ["-af", "aresample=48000,afade=t=in:d=0.02", "-ac", "2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
               "-c:a", "aac", "-b:a", "256k"]
        if p.get("zeig"):  # Regel 114: stummes Zeige-Stück (Bild der Handlung, kein Ton – die Pipeline legt Musik darunter)
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{s0:.3f}", "-to", f"{s1:.3f}", "-i", c["file"], "-f", "lavfi", "-t", f"{s1 - s0:.3f}",
                 "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v:0", "-map", "1:a:0", "-vf", vf, "-t", f"{s1 - s0:.3f}"] + enc + [out])
            if zeigen and abs(zeigen[-1][1] - t) < 0.01:
                zeigen[-1][1] = round(t + s1 - s0, 3)
            else:
                zeigen.append([round(t, 3), round(t + s1 - s0, 3)])
        elif p.get("shift"):  # Ton des Satzes, Bild aus der Handlung danach (Regel 111)
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{s0:.3f}", "-to", f"{s1:.3f}", "-i", c["file"],
                 "-ss", f"{s0 + p['shift']:.3f}", "-t", f"{s1 - s0:.3f}", "-i", c["file"], "-map", "1:v:0", "-map", "0:a:0",
                 "-vf", vf, "-t", f"{s1 - s0:.3f}"] + enc + [out])
        else:
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{s0:.3f}", "-to", f"{s1:.3f}", "-i", c["file"], "-vf", vf] + enc + [out])
        for w in ([] if p.get("zeig") else ws):
            ref_s = sent_of[id(w)]
            if id(w) in first_of:
                anchors[ref_s] = t + (w["s"] - s0)
            wanchors.setdefault(ref_s, []).append((w["t"], t + (w["s"] - s0)))
            anchors.setdefault(ref_s, t + (w["s"] - s0))
        files_c.append(out)
        if n and not (p.get("zeig") and subs[n - 1].get("zeig")):
            joins.append(round(t, 3))  # hier stoßen zwei Clips aneinander → in der Pipeline immer ein Schnitt
        t += s1 - s0
        if p.get("extra"):  # die Handlung läuft nach dem Satz noch weiter → stumm anhängen (im Original redet er dort oft schon
            ea, eb = p["extra"]  # weiter); die Pipeline lässt diese Strecke stehen und legt Musik darunter (plan „zeigen“)
            out2 = os.path.join(odir, f"teil_{n:02d}_h.mp4")
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{ea:.3f}", "-to", f"{eb:.3f}", "-i", c["file"], "-f", "lavfi", "-t", f"{eb - ea:.3f}",
                 "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v:0", "-map", "1:a:0", "-vf", vf, "-t", f"{eb - ea:.3f}"] + enc + [out2])
            files_c.append(out2)
            zeigen.append([round(t, 3), round(t + eb - ea, 3)])
            t += eb - ea  # bewusst KEINE Clip-Grenze in „joins“: Bild läuft nahtlos weiter, die Pipeline soll hier nicht trennen
    lst = os.path.join(odir, "teile.txt")
    open(lst, "w").write("".join(f"file '{f}'\n" for f in files_c))
    src = os.path.join(odir, "montage.mp4")
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", src])
    for f in files_c + [lst]:
        os.remove(f)

    # B-Roll-Stücke vorbereiten (9:16, ohne Ton) und an die Satz-Zeiten in der zusammengesetzten Datei hängen
    brolls = []
    for n, b in enumerate((plan or {}).get("broll") or []):
        m = next((m for m in mats if m["id"] == (b.get("material") or "").strip()), None)
        at = at_word(b.get("ueber"), b.get("wort"))
        if not m or at is None:
            continue
        c = m["c"]
        if schnell and at > t - 8.0:
            # Inhaber 07.10.: Im Schlusssatz sprang das Bild zwischen Sprecher und „Junior Box auf dem Tisch“ hin und her.
            # Über Begrüßung/Schluss mit Sprecher im Bild liegt kein Zwischenbild – entweder er oder das Produkt, kein Wechsel.
            log(f"  Zwischenbild über dem Schlusssatz weggelassen ({c['id']})")
            continue
        if schnell and c.get("talk"):
            # Inhaber 07.10.: Nach dem fertigen Burger kam als Zwischenbild noch einmal „Soße aufs Brötchen“, nach dem Einpacken
            # „Pommes in die Schachtel“ – Arbeitsschritte außerhalb der Reihenfolge. In einer Zubereitung kommen Zwischenbilder
            # deshalb nur aus reinen Bild-Clips (fertiges Menü, Laden), nie aus Clips, in denen gearbeitet oder gesprochen wird.
            log(f"  Zwischenbild aus {c['id']} weggelassen (Arbeitsschritt außerhalb der Reihenfolge)")
            continue
        d = min(2.5, max(1.0, float(b.get("dauer_s") or 1.8)), m["b"] - m["a"])
        a = m["a"] + max(0.0, (m["b"] - m["a"] - d) / 2)  # ruhige Mitte der Stelle
        e = a + d
        if e - a < 0.8:
            continue
        out = os.path.join(odir, f"broll_{n}.mp4")
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-to", f"{e:.3f}", "-i", c["file"], "-an", "-vf",
             "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,setsar=1,format=yuv420p",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", out])
        brolls.append({"file": out, "t_src": max(0.0, at - 0.05), "dur": round(e - a, 2), "clip": c["id"], "was": m.get("inhalt", "")[:60]})
    stickers = []
    for b in (plan or {}).get("bilder") or []:
        pic = next((p for p in pics if p["id"] == b.get("bild")), None)
        at = at_word(b.get("bei"), b.get("wort"))
        if pic and at is not None:
            stickers.append({"file": pic["file"], "t_src": at, "dur": 1.8, "alpha": pic["alpha"], "was": pic.get("inhalt", "")[:60]})
    plan_out = {"format": (plan or {}).get("format", ""), "titel": (plan or {}).get("titel", ""), "ziel_s": (plan or {}).get("ziel_s"),
                "clips": [{k: c.get(k) for k in ("id", "file", "dur", "talk", "inhalt", "qualitaet", "broll_ok")} for c in clips],
                "sprechteile": [f"{c['id']}.{k}" for c, k in order], "joins": joins, "zeigen": zeigen, "broll": brolls, "bilder": stickers}
    plan_path = os.path.join(odir, "plan.json")
    json.dump(plan_out, open(plan_path, "w"), ensure_ascii=False, indent=1)
    return {"src": src, "plan": plan_path, "stickers": stickers}


_alle = (plans or {}).get("geschichten") or []
stories = [g for g in _alle if g.get("sprechteile")][:max(1, A.clips)]
log(f"Geschichten von der KI: {len(_alle)} (höchstens {max(1, A.clips)} erlaubt) – "
    + "; ".join(f"„{str(g.get('titel'))[:40]}“ {len(g.get('sprechteile') or [])} Sätze, {len(g.get('bilder') or [])} Bilder, "
                f"{len(g.get('broll') or [])} B-Rolls" for g in _alle[:5]))
_vorher = [(str(g.get("titel"))[:40], len(g["sprechteile"])) for g in stories]
for g in stories:  # Regel 82: nur Clips aus dem Thema dieser Geschichte (Sätze/B-Roll aus fremden Clips fliegen raus)
    own = {str(c).strip() for c in g.get("clips") or []}
    if own:
        keep = [x for x in g["sprechteile"] if x.split(".")[0].strip() in own]
        if keep:
            g["sprechteile"] = keep
        # B-Roll aus stummen Clips bleibt erlaubt – raus nur B-Roll aus Sprech-Clips eines ANDEREN Themas
        fremd = {c["id"] for c in clips if c.get("talk")} - own
        g["broll"] = [b for b in g.get("broll") or [] if str(b.get("material", "")).split("w")[0].strip() not in fremd]
used, results = set(), []
for g in stories:  # kein Satz in zwei Videos
    g["sprechteile"] = [x for x in g["sprechteile"] if x.strip() not in used]
    used.update(x.strip() for x in g["sprechteile"])
_nach = {id(g): sum(1 for x in g["sprechteile"] if ref(x)) for g in stories}
for (titel, n0), g in zip(_vorher, stories):  # nachvollziehbar: was die Filter (Thema, doppelte Sätze, zu kurz) übrig lassen
    if _nach[id(g)] < 2 or _nach[id(g)] < n0:
        log(f"  „{titel}“: {n0} → {_nach[id(g)]} gültige Sätze nach den Filtern" + (" → VERWORFEN (unter 2 Sätze)" if _nach[id(g)] < 2 else ""))
stories = [g for g in stories if sum(1 for x in g["sprechteile"] if ref(x)) >= 2] or stories[:1] or [None]


def puzzle(g):
    """Inhaber 08.10. (Halal-Ordner): „Da fehlt ein Stück vom Video … die Software muss wirklich ALLE Videos aus dem Ordner
    anschauen und sie zusammenfügen wie Puzzleteile – wenn der Anfang oder das Ende fehlt, ergibt das Video keinen Sinn.“
    Ursache: Die Planung hatte den dritten Clip (Angebot/Einladung = der Schluss) als „anderes Thema“ aussortiert.
    Deshalb eine zweite, gezielte Prüfung: Jeder Sprech-Clip, aus dem KEIN Satz im Plan steht, wird der KI einzeln vorgelegt –
    fehlt dem Video ein Anfang, eine Fortsetzung oder der Schluss, der in diesem Clip liegt?"""
    alt = [x.strip() for x in g.get("sprechteile") or [] if ref(x)]
    benutzt = {x.split(".")[0] for x in alt}
    offen = [c for c in talk if c["id"] not in benutzt and len(c.get("sents") or []) >= 2]
    if not alt or not offen or len(talk) < 2:
        return
    # Test Malo's-Ordner 08.10.: Der Plan war mit 55 Sätzen schon ein ganzes Video (Zubereitung der Junior Box) – die Prüfung
    # hängte trotzdem „Das ist der beste Burger in Mannheim. Hier bei Malo's in Neckarau.“ aus einem ANDEREN Video hinten an.
    # Fehlende Teile sucht sie deshalb nur, solange das geplante Video kurz ist (unter 40 s Sprache) – ein langes hat sein Ende.
    geplant = sum(c["sents"][k][-1]["e"] - c["sents"][k][0]["s"] for c, k in (ref(x) for x in alt))
    if geplant >= 40.0:
        log(f"Puzzle-Prüfung: übersprungen – der Plan hat schon {geplant:.0f} s Sprache")
        return
    txt = lambda c, k: " ".join(w["t"] for w in c["sents"][k])
    zeilen = ["## AKTUELLER SCHNITTPLAN (in dieser Reihenfolge)"]
    zeilen += [f"  {x}  {txt(*ref(x))}" for x in alt]
    zeilen.append("## NICHT VERWENDETE CLIPS")
    for c in offen:
        zeilen.append(f"### {c['id']} ({c['dur']:.0f} s) – Bild: {c.get('inhalt', '?')}")
        zeilen += [f"  {c['id']}.{k} [{st[0]['s']:.1f}–{st[-1]['e']:.1f}s] {' '.join(w['t'] for w in st)}" for k, st in enumerate(c["sents"])]
    if os.environ.get("VELOX_DEBUG"):
        log("\n".join(zeilen))
    res = gemini(
        "Du bist Cutter. Der Kunde hat EINEN Ordner mit Rohclips für EIN Kurzvideo hochgeladen – die Clips sind Puzzleteile "
        "dieses einen Videos. Der Schnittplan unten verwendet einige Clips gar nicht. Prüfe jeden nicht verwendeten Clip: "
        "Liegt darin ein Teil, der dem Video FEHLT – der Anfang (Aufhänger, Begrüßung), eine Fortsetzung oder das ENDE "
        "(Angebot, Einladung, Challenge, Aufruf, Verabschiedung)? Gleiche Person bzw. gleicher Dreh und inhaltlich "
        "anschließend = gehört dazu, auch wenn es ein neuer Gedanke ist (ein Angebot am Schluss ist kein anderes Thema). "
        "Dann füge diese Sätze an der richtigen Stelle ein. Regeln: je Aussage nur die LETZTE saubere Fassung (im Rohmaterial "
        "stehen viele Fehlversuche derselben Sätze hintereinander); keine Regie-Anweisungen, Absprachen oder Sätze an den "
        "Kameramann; nichts, was schon im Plan gesagt wird; ein mehrteiliges Angebot VOLLSTÄNDIG (Ankündigung + Einladung + "
        "Bedingung/Belohnung), nie nur ein Teil davon. Die erkannten Texte können Hörfehler enthalten („Schaf“ statt „scharf“) – "
        "das ist kein Grund, einen Satz wegzulassen. Enthält ein Clip nur Wiederholungen, Regie oder wirklich ein anderes "
        "Thema (anderes Produkt, anderer Anlass), bleibt er draußen. Antworte mit der Liste der NEUEN Sätze: je Satz seine "
        "Nummer (z. B. C2.14) und die Nummer des Satzes aus dem aktuellen Plan, HINTER dem er stehen soll (oder „ANFANG“, wenn "
        "er vor allem anderen kommt). Mehrere neue Sätze hintereinander bekommen dasselbe „nach“ und stehen in Sprech-"
        "Reihenfolge. Fehlt nichts: ergaenzen=false und leere Liste.",
        [{"text": "\n".join(zeilen)}],
        {"type": "object", "properties": {"ergaenzen": {"type": "boolean"}, "grund": {"type": "string"},
                                          "neu": {"type": "array", "items": {"type": "object", "properties": {
                                              "satz": {"type": "string"}, "nach": {"type": "string"}}, "required": ["satz", "nach"]}}},
         "required": ["ergaenzen", "grund", "neu"]}, timeout=120, deep=True)
    if not res or not res.get("ergaenzen"):
        log(f"Puzzle-Prüfung: {len(offen)} ungenutzte Sprech-Clips angesehen – nichts fehlt ({str((res or {}).get('grund'))[:120]})")
        return
    nr_ = lambda t: (re.search(r"C\d+\.\d+", str(t) or "") or [None])[0]
    nach, dazu = {}, []
    for d in res.get("neu") or []:
        x, hinter = nr_(d.get("satz")), nr_(d.get("nach"))
        if not x or not ref(x) or x in alt or x in dazu or x.split(".")[0] in benutzt:
            continue
        nach.setdefault(hinter if hinter in alt else (None if "ANFANG" in str(d.get("nach")).upper() else alt[-1]), []).append(x)
        dazu.append(x)
    if not dazu or len(dazu) > 10:
        log(f"Puzzle-Prüfung: Vorschlag der KI nicht übernommen ({len(dazu)} neue Sätze; Antwort: {str(res.get('neu'))[:200]})")
        return
    neu = list(nach.get(None, []))
    for x in alt:
        neu.append(x)
        neu += nach.get(x, [])
    # Zweiter Blick je ergänztem Clip (Test 08.10.: übernommen wurde nur „Wenn du es ohne Milch schaffst, geht alles aufs Haus“ –
    # ohne „Mein Angebot an dich. Ich lade dich gern bei uns zum Essen ein. Echtes Thai scharf zu essen.“ davor hängt der
    # Satz in der Luft). Der Gedanke wird vom ersten bis zum letzten Satz geholt, jede Aussage in der letzten sauberen Fassung.
    for cid in sorted({x.split(".")[0] for x in dazu}):
        c = next(c for c in talk if c["id"] == cid)
        gew = [x for x in dazu if x.split(".")[0] == cid]
        r2 = gemini(
            "Du bist Cutter. Aus einem Rohclip wurden die markierten Sätze als Teil (meist Schluss) eines Kurzvideos ausgewählt. "
            "Im Rohclip probt der Sprecher denselben Gedanken mehrfach; dazwischen stehen Regie-Anweisungen. Prüfe: Ist der "
            "Gedanke mit den markierten Sätzen VOLLSTÄNDIG – oder beginnt er im Clip früher (Ankündigung wie „Mein Angebot an "
            "dich“, Einladung, Bedingung) bzw. geht er noch weiter? Gib die vollständige Satzfolge dieses einen Gedankens zurück, "
            "in Sprech-Reihenfolge: jede Aussage genau einmal, jeweils in ihrer LETZTEN sauberen Fassung; keine Regie-Sätze, "
            "keine Fehlversuche, keine Sätze an den Kameramann. Hörfehler im Text („Schaf“ statt „scharf“) sind kein Grund, "
            "einen Satz wegzulassen. Höchstens 6 Sätze. Die markierten Sätze (oder eine spätere, sauberere Fassung davon) gehören dazu.",
            [{"text": f"### {cid}\n" + "\n".join(
                f"  {cid}.{k}{'  ← AUSGEWÄHLT' if f'{cid}.{k}' in gew else ''}  {' '.join(w['t'] for w in st)}" for k, st in enumerate(c["sents"]))}],
            {"type": "object", "properties": {"saetze": {"type": "array", "items": {"type": "string"}}, "grund": {"type": "string"}},
             "required": ["saetze", "grund"]}, timeout=90, deep=True)
        folge = []
        for x in (r2 or {}).get("saetze") or []:
            x = (x or "").strip()
            if ref(x) and x.split(".")[0] == cid and x not in folge and x not in alt:
                folge.append(x)
        folge.sort(key=lambda x: int(x.split(".")[1]))
        log(f"Puzzle-Prüfung, zweiter Blick {cid}: {(r2 or {}).get('saetze')} – {str((r2 or {}).get('grund'))[:160]}")
        if os.environ.get("VELOX_DEBUG"):
            for k, st in enumerate(c["sents"]):
                log(f"      {cid}.{k} {' '.join(w['t'] for w in st)}")
        if len(folge) > len(gew) and len(folge) <= 6:
            i0 = min(neu.index(x) for x in gew)
            neu = [x for x in neu if x not in gew]
            neu[i0:i0] = folge
            dazu = [x for x in dazu if x not in gew] + folge
            log(f"Puzzle-Prüfung: Gedanke aus {cid} vervollständigt ({', '.join(gew)} → {', '.join(folge)})")
    g["sprechteile"] = neu
    g["clips"] = sorted({str(c).strip() for c in g.get("clips") or []} | {x.split(".")[0] for x in dazu})
    log(f"Puzzle-Prüfung: {len(dazu)} Sätze aus bisher ungenutzten Clips ergänzt ({', '.join(dazu)}) – {str(res.get('grund'))[:160]}")
    for x in dazu:
        log(f"    + {x} „{txt(*ref(x))[:70]}“")


if stories[0] and len(stories) == 1:   # ein Ordner = ein Video: fehlende Puzzleteile suchen
    try:
        puzzle(stories[0])
    except Exception as e:
        log(f"Puzzle-Prüfung übersprungen: {e}")
for n, g in enumerate(stories):
    if len(stories) > 1:
        stage(f"Setzt Video {n + 1} von {len(stories)} zusammen …")
    results.append(build(g, A.out if n == 0 else os.path.join(A.out, f"video{n + 1}")))
for p in pics:  # ungenutzte Bilder wegräumen
    if not any(st["file"] == p["file"] for r in results for st in r["stickers"]):
        os.remove(p["file"])
log(f"KI-Verbrauch Montage: {TOK}")
for r in results:  # die Pipeline zählt den Montage-Verbrauch zu ihrem eigenen dazu (jobs.jsonl → ki_tokens)
    pj = json.load(open(r["plan"]))
    pj["ki_tokens"] = TOK if r is results[0] else {"aufrufe": 0, "ein": 0, "aus": 0, "denken": 0}
    json.dump(pj, open(r["plan"], "w"), ensure_ascii=False, indent=1)
print(json.dumps({"src": results[0]["src"], "plan": results[0]["plan"],
                  "videos": [{"src": r["src"], "plan": r["plan"]} for r in results]}), flush=True)
