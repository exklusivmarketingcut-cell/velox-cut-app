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
    ws = [{"t": w["word"].strip(), "s": float(w["start"]), "e": float(w["end"]), "p": float(w.get("probability", 1))}
          for sg in r["segments"] for w in sg.get("words", []) if w["word"].strip()]
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
    for w in ws:
        cur.append(w)
        if SENT_END.search(w["t"]):
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
                 "gesprochenem Text (Produkt, Essen, Zubereitung, Laden, Schild – gutes, ruhiges Bild; KEIN Gesicht im Gespräch).",
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
    "Belegen war falsch). Keine angefangenen oder abgebrochenen Sätze – ganz oder gar nicht. Keine Einstellung, "
    "die nur kurz aufblitzt (unter ~1,5 s) zwischen zwei anderen. Jede neue Einstellung beginnt mit der HANDLUNG (Hand greift "
    "in die Gurken, Patty wird vom Grill gekratzt), nicht mit dem Weg der Kamera dorthin. "
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


def build(plan, odir):
    """Eine Geschichte → montage.mp4 + plan.json in <odir>."""
    os.makedirs(odir, exist_ok=True)
    order = [r for r in (ref(x) for x in (plan or {}).get("sprechteile") or []) if r]
    if not order:  # Rückfall ohne KI: alle Sprech-Clips der Reihe nach
        order = [(c, k) for c in talk for k in range(len(c["sents"]))]
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
            + ("  ← WENIG: großer Teil dieses Clips fehlt im Video" if c["dur"] >= 20 and anteil < 0.5 else "")
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
    files_c, t, anchors, joins, wanchors = [], 0.0, {}, [], {}
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
        out = os.path.join(odir, f"teil_{n:02d}.mp4")
        vf = f"scale={TW}:{TH}:force_original_aspect_ratio=increase,crop={TW}:{TH},fps={FPS:.3f},setsar=1"
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{s0:.3f}", "-to", f"{s1:.3f}", "-i", c["file"], "-vf", vf,
             "-af", "aresample=48000,afade=t=in:d=0.02", "-ac", "2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
             "-c:a", "aac", "-b:a", "256k", out])
        for w in ws:
            ref_s = sent_of[id(w)]
            if id(w) in first_of:
                anchors[ref_s] = t + (w["s"] - s0)
            wanchors.setdefault(ref_s, []).append((w["t"], t + (w["s"] - s0)))
            anchors.setdefault(ref_s, t + (w["s"] - s0))
        files_c.append(out)
        if n:
            joins.append(round(t, 3))  # hier stoßen zwei Clips aneinander → in der Pipeline immer ein Schnitt
        t += s1 - s0
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
                "sprechteile": [f"{c['id']}.{k}" for c, k in order], "joins": joins, "broll": brolls, "bilder": stickers}
    plan_path = os.path.join(odir, "plan.json")
    json.dump(plan_out, open(plan_path, "w"), ensure_ascii=False, indent=1)
    return {"src": src, "plan": plan_path, "stickers": stickers}


stories = [g for g in ((plans or {}).get("geschichten") or []) if g.get("sprechteile")][:max(1, A.clips)]
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
stories = [g for g in stories if sum(1 for x in g["sprechteile"] if ref(x)) >= 2] or stories[:1] or [None]
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
