"""Velox Cut – Musik-Schnitt: Ordner nur mit B-Roll (Zubereitung, Essen, Laden – niemand spricht) → EIN Video auf den Beat.

Wie die Food-Edits des Nutzers (SCHNITTREGELN.md): Schnitt alle ~0,7–1 s genau auf den Beat, Aufhänger zuerst (stärkstes
Bild), dann die Zubereitung in der richtigen Reihenfolge, am Ende das fertige Produkt etwas länger. Keine Untertitel, nur
Titel-Karte oben (wie im normalen Schnitt) und Logo. Musik lizenzfrei aus schnitt-maschine/musik, Originalton (Brutzeln)
leise darunter.

  python musikschnitt.py --out <ordner> [--style viral] [--music auto|<name>] [--logo logo.png] <datei oder ordner> [...]
Ausgabe (letzte Zeile, JSON) wie pipeline.py: {"clips": [{"file", "title", "seconds", "songs"}]}
"""
import argparse, ast, base64, json, os, re, subprocess, sys, time, urllib.request
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ap.add_argument("--style", default="viral", choices=["clean", "viral", "premium"])
ap.add_argument("--music", default="auto")
ap.add_argument("--logo", default="")
ap.add_argument("--words", default="")
ap.add_argument("inputs", nargs="+")
A = ap.parse_args()
A.out = os.path.abspath(A.out)
os.makedirs(A.out, exist_ok=True)
MASCHINE = os.environ.get("VELOX_MASCHINE", os.path.join(HERE, "..", "..", "schnitt-maschine"))
MUSIC_DIR = os.environ.get("VELOX_MUSIK", os.path.join(MASCHINE, "musik"))
KEY = os.environ.get("GEMINI_API_KEY", "").strip()
VIDEO = (".mp4", ".mov", ".m4v", ".mts", ".avi", ".mkv", ".webm")
TW, TH, FPS = 1080, 1920, 30
STATS = {"art": "musikschnitt", "style": A.style}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def stage(text, p=None):
    print(f"STAGE {text}", flush=True)
    if p is not None:
        print(f"PROGRESS {p}", flush=True)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(((r.stderr or r.stdout).strip().splitlines() or [cmd[0]])[-1])
    return r


def gemini(system, parts, schema, timeout=120):
    if not KEY:
        return None
    body = json.dumps({"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": parts}],
                       "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json", "responseSchema": schema}}).encode()
    for model in ("gemini-flash-latest", "gemini-3-flash-preview", "gemini-2.5-flash"):
        for _ in range(2):
            try:
                req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", data=body,
                                             headers={"x-goog-api-key": KEY, "Content-Type": "application/json"})
                d = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
                return json.loads("".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"]))
            except Exception as e:  # noqa: BLE001
                log(f"Gemini {model}: {str(e)[:100]}")
    return None


# Titel-Karte, Emojis und Musikliste 1:1 aus pipeline.py übernehmen (gleiches Aussehen wie der normale Schnitt)
_src = open(os.path.join(HERE, "pipeline.py"), encoding="utf-8").read()
_ns = {"os": os, "re": re, "LOUD": A.style != "clean", "FONTS": os.environ.get("VELOX_FONTS", os.path.join(MASCHINE, "fonts"))}
for node in ast.parse(_src).body:
    names = [n.id for t in getattr(node, "targets", []) for n in ast.walk(t) if isinstance(n, ast.Name)]
    if (isinstance(node, ast.FunctionDef) and node.name == "hook_card") or set(names) & {"EMOJIS", "APPLE_EMOJI", "MUSIC", "HOOK_Y", "LOGO_PX", "LOGO_Y"}:
        exec(compile(ast.Module([node], []), "pipeline.py", "exec"), _ns)
hook_card, MUSIC, HOOK_Y, LOGO_PX, LOGO_Y = _ns["hook_card"], _ns["MUSIC"], _ns["HOOK_Y"], _ns["LOGO_PX"], _ns["LOGO_Y"]

# ─────────────────────────────── 1. Clips einsammeln ───────────────────────────────
files = []
for p in A.inputs:
    if os.path.isdir(p):
        for root, _, fs in os.walk(p):
            files += [os.path.join(root, f) for f in sorted(fs)]
    else:
        files.append(p)
videos = sorted(f for f in files if f.lower().endswith(VIDEO) and not os.path.basename(f).startswith((".", "._")))


def probe(path):
    d = json.loads(run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration", "-of", "json", path]).stdout)
    if not any(s["codec_type"] == "video" for s in d["streams"]):
        return None
    return {"dur": float(d["format"].get("duration") or 0), "audio": any(s["codec_type"] == "audio" for s in d["streams"])}


clips = []
for f in videos:
    info = probe(f)
    if info and info["dur"] >= 0.8:
        clips.append({"id": f"C{len(clips) + 1}", "file": f, **info})
if not clips:
    sys.exit("Im Ordner ist kein lesbares Video.")
stage(f"Schaut sich {len(clips)} Clips an …", 5)

# ─────────────────────────────── 2. Stellen (je ~2,5 s) ansehen ───────────────────────────────
# Jeder Clip wird in Stellen zerlegt; je Stelle ein Bild. Unscharfes fliegt schon hier raus (Kantenschärfe).
import cv2  # noqa: E402

shots = []
for c in clips:
    n = max(1, min(8, int(c["dur"] // 2.5)))
    step = c["dur"] / n
    for k in range(n):
        a, b = k * step + 0.15, (k + 1) * step - 0.15
        if b - a < 0.6:
            continue
        p = os.path.join(A.out, f"s_{c['id']}_{k}.jpg")
        try:
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{(a + b) / 2:.2f}", "-i", c["file"], "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", p])
        except RuntimeError:
            continue
        g = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
        sharp = float(cv2.Laplacian(g, cv2.CV_64F).var()) if g is not None else 0
        shots.append({"id": f"{c['id']}s{k + 1}", "c": c, "a": round(a, 2), "b": round(b, 2), "img": p, "sharp": sharp})
if len(shots) > 70:  # zu viele Bilder für einen KI-Aufruf → die schärfsten je Clip behalten
    keep = sorted(shots, key=lambda s: -s["sharp"])[:70]
    shots = [s for s in shots if s in keep]
med_sharp = float(np.median([s["sharp"] for s in shots])) if shots else 0
log(f"{len(shots)} Stellen, Schärfe-Median {med_sharp:.0f}")

stage("Plant den Musik-Schnitt …", 15)
parts = []
for s in shots:
    parts += [{"text": f"Stelle {s['id']} ({s['b'] - s['a']:.1f} s{', eher unscharf' if s['sharp'] < med_sharp * 0.35 else ''}):"},
              {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(open(s["img"], "rb").read()).decode()}}]
musik = {n: d for n, d in MUSIC.items() if os.path.exists(os.path.join(MUSIC_DIR, f"{n}.mp3"))}
plan = gemini(
    "Du bist Cutter für TikTok/Reels von Restaurants und Läden. Aus diesen B-Roll-Stellen (niemand spricht) baust du EIN "
    "Musik-Video, geschnitten auf den Beat. Wähle 10–18 Stellen und ordne sie wie ein Profi:\n"
    "1) AUFHÄNGER zuerst: das stärkste, appetitlichste Bild (Käse zieht Fäden, Biss, Flamme, Soße läuft, fertiges Produkt).\n"
    "2) Dann die Zubereitung in der echten Reihenfolge (roh → braten/backen → belegen → anrichten/verpacken).\n"
    "3) Ende: das fertige Produkt groß (Held), gern danach Laden/Schild/Theke. Held und Ende dürfen länger stehen (lang=true).\n"
    "Keine zwei fast gleichen Bilder hintereinander, nichts Unscharfes/Verwackeltes/Dunkles, keine Leute, die in die Kamera "
    "reden. Titel: kurzer, neugierig machender Aufhänger auf Deutsch (max. 6 Wörter) mit EINEM passenden Emoji am Ende, "
    "z. B. „So entsteht unser Smashburger 🍔“. Musik: passend zur Stimmung aus der Liste."
    + (f" Namen/Marken des Kunden (Schreibweise): {A.words}." if A.words else ""),
    parts + [{"text": "Musik:\n" + "\n".join(f"{n}: {d}" for n, d in musik.items())}],
    {"type": "object", "properties": {
        "titel": {"type": "string"}, "musik": {"type": "string", "enum": list(musik) or ["keine"]},
        "stellen": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "was": {"type": "string"}, "lang": {"type": "boolean"}}, "required": ["id", "was", "lang"]}}},
     "required": ["titel", "musik", "stellen"]})
for s in shots:
    os.remove(s["img"])
by_id = {s["id"]: s for s in shots}
order = []
for x in (plan or {}).get("stellen") or []:
    s = by_id.get((x.get("id") or "").strip())
    if s and all(o["s"] is not s for o in order):
        order.append({"s": s, "was": x.get("was", ""), "lang": bool(x.get("lang"))})
if len(order) < 4:  # KI nicht erreichbar → einfach die schärfsten Stellen in Dateireihenfolge
    order = [{"s": s, "was": "", "lang": False} for s in sorted(shots, key=lambda s: -s["sharp"])[:12]]
    order.sort(key=lambda o: (o["s"]["c"]["file"], o["s"]["a"]))
    order[-1]["lang"] = True
title = ((plan or {}).get("titel") or "").strip()
log("Plan:", title, "|", " → ".join(f"{o['s']['id']}{'*' if o['lang'] else ''} {o['was'][:25]}" for o in order))

# ─────────────────────────────── 3. Musik: Beats finden ───────────────────────────────
name = A.music if A.music in musik else (plan or {}).get("musik")
if name not in musik:
    name = {"clean": "lofi", "viral": "hiphop-upbeat", "premium": "hiphop-groove"}[A.style]
song = os.path.join(MUSIC_DIR, f"{name}.mp3")
STATS["music"] = name
SR, HOP = 22050, 512
pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", song, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"], capture_output=True).stdout
y = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
frames = np.lib.stride_tricks.sliding_window_view(y, 2048)[::HOP] * np.hanning(2048)
spec = np.log1p(np.abs(np.fft.rfft(frames, axis=1))[:, :400])
flux = np.maximum(0, np.diff(spec, axis=0)).sum(axis=1)
flux = (flux - flux.mean()) / (flux.std() + 1e-9)
fr = SR / HOP  # Onset-Werte pro Sekunde
ac = np.correlate(flux[: int(60 * fr)], flux[: int(60 * fr)], "full")[len(flux[: int(60 * fr)]) - 1:]
lags = np.arange(int(fr * 60 / 180), int(fr * 60 / 70))
w = np.exp(-0.5 * (np.log2(60 * fr / lags / 115) / 0.6) ** 2)  # leicht Richtung ~115 BPM (typischer Hip-Hop/Pop)
lag = int(lags[np.argmax(ac[lags] * w)])
beat = lag / fr
rms = np.sqrt(np.convolve(y ** 2, np.ones(SR // 10) / (SR // 10), "same"))[:: SR // 10]
start = next((i / 10 for i, v in enumerate(rms) if v > 0.5 * np.median(rms)), 0.0)  # leises Intro überspringen
i0 = int(start * fr)
phase = max(range(lag), key=lambda o: flux[i0 + o:: lag][:64].sum())
t_first = (i0 + phase) / fr
beats = np.arange(t_first, len(y) / SR - 0.5, beat)
STATS["bpm"] = round(60 / beat, 1)
log(f"Musik {name}: {60 / beat:.0f} BPM, erster Beat {t_first:.2f} s")

# Länge je Bild in Beats: ~0,7–1 s normal, Held/Ende ~2 s, Aufhänger ~1,3 s
def nbeats(target):
    return max(1, round(target / beat))


plan_b = []
for k, o in enumerate(order):
    tgt = 1.9 if o["lang"] else (1.3 if k == 0 else 0.85)
    if k == len(order) - 1:
        tgt = max(tgt, 2.2)
    plan_b.append(nbeats(tgt))
while sum(plan_b) * beat > 22 and len(order) > 6:  # max ~22 s
    del order[-2], plan_b[-2]
cuts = [0]
for n in plan_b:
    cuts.append(cuts[-1] + n)
total = cuts[-1] * beat

# ─────────────────────────────── 4. Bilder schneiden (exakt auf die Beats) ───────────────────────────────
stage("Schneidet auf den Beat …", 35)
parts_v = []
for k, o in enumerate(order):
    s = o["s"]
    t0, t1 = cuts[k] * beat, cuts[k + 1] * beat
    nf = round(t1 * FPS) - round(t0 * FPS)  # Bildgenau: Rundungsfehler laufen nicht auf
    d = nf / FPS
    avail = s["b"] - s["a"]
    speed = 1.0 if avail >= d else max(avail / d, 0.6)  # zu kurz → leichte Zeitlupe (sieht bei Food gut aus)
    src_len = min(avail, d * speed)
    a = s["a"] + max(0.0, (avail - src_len) / 2)
    z0, z1 = (1.0, 1.07) if k % 2 == 0 else (1.07, 1.0)  # sanfter Push-in / Pull-out im Wechsel
    zoom = f"{z0}+({z1}-{z0})*t/{d:.3f}"
    vf = (f"setpts=PTS/{speed:.4f},fps={FPS},scale={TW}:{TH}:force_original_aspect_ratio=increase,crop={TW}:{TH},"
          f"scale=w='trunc({TW}*({zoom})/2)*2':h='trunc({TH}*({zoom})/2)*2':eval=frame,crop={TW}:{TH},setsar=1,format=yuv420p")
    out = os.path.join(A.out, f"m_{k:02d}.mp4")
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{src_len + 0.3:.3f}", "-i", s["c"]["file"]]
    if s["c"]["audio"]:
        af = f"atempo={max(speed, 0.5):.4f},apad,atrim=0:{d:.4f},aresample=48000"
        cmd += ["-vf", vf, "-af", af]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-vf", vf, "-map", "0:v", "-map", "1:a", "-shortest"]
    cmd += ["-frames:v", str(nf), "-t", f"{d:.4f}", "-ac", "2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", out]
    run(cmd)
    parts_v.append(out)
    print(f"PROGRESS {35 + int(45 * (k + 1) / len(order))}", flush=True)
lst = os.path.join(A.out, "m_liste.txt")
open(lst, "w").write("".join(f"file '{f}'\n" for f in parts_v))
body = os.path.join(A.out, "m_body.mp4")
run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", body])
for f in parts_v + [lst]:
    os.remove(f)

# ─────────────────────────────── 5. Titel, Logo, Musik → fertig ───────────────────────────────
stage("Legt Musik, Titel und Logo an …", 85)
final = os.path.join(A.out, "clip-1.mp4")
inputs = ["-i", body, "-ss", f"{t_first:.3f}", "-i", song]
fc = ["[0:v]eq=contrast=1.04:saturation=1.10[v0]" if A.style != "clean" else "[0:v]null[v0]"]
vin, ni = "v0", 2
card = hook_card(title, os.path.join(A.out, "titel.png")) if title else None
logo = A.logo if A.logo and os.path.exists(A.logo) else None
hy = HOOK_Y + (LOGO_PX + 20 if logo else 0)
if card:
    inputs += ["-loop", "1", "-t", "3.3", "-i", card]
    fc.append(f"[{ni}:v]format=rgba,fade=t=in:st=0:d=0.3:alpha=1,fade=t=out:st=2.9:d=0.4:alpha=1[hc]")
    fc.append(f"[{vin}][hc]overlay=x=(W-w)/2:y='{hy}-36*pow(1-clip(t/0.35,0,1),3)':eof_action=pass[vh]")
    vin, ni = "vh", ni + 1
if logo:
    inputs += ["-loop", "1", "-i", logo]
    fc.append(f"[{ni}:v]format=rgba,scale={LOGO_PX}:-1:flags=lanczos,fade=t=in:st=0.1:d=0.35:alpha=1[lg]")
    fc.append(f"[{vin}][lg]overlay=x=48:y={LOGO_Y}:shortest=1[vl]")
    vin, ni = "vl", ni + 1
# Musik vorne, Originalton (Brutzeln, Messer) leise darunter; weich raus; Limiter
fc.append(f"[1:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,aresample=48000,volume=0.9,"
          f"afade=t=out:st={max(0, total - 1.2):.3f}:d=1.2[mu]")
fc.append("[0:a]volume=0.22[amb]")
fc.append("[mu][amb]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]")
run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(fc), "-map", f"[{vin}]", "-map", "[a]",
     "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS),
     "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", final])
os.remove(body)
if card and os.path.exists(card):
    os.remove(card)


# ─────────────────────────────── 6. Song-Vorschläge (wie pipeline.py) ───────────────────────────────
def suggest_songs():
    try:
        songs = json.load(open(os.path.join(MASCHINE, "trends.json"))).get("songs") or []
    except Exception:  # noqa: BLE001
        return []
    if not songs:
        return []
    res = gemini("Du bist Social-Media-Manager. Schlage für dieses Kurzvideo (Food-/Laden-B-Roll auf Musik, keine Sprache) 3 "
                 "Trend-Songs vor, die der Kunde beim Posten auf TikTok als Sound hinzufügen kann. Nur Songs, die zu Stimmung und "
                 "einer seriösen Marke passen. Bevorzuge Aufsteiger (discovery). Je Song eine kurze Begründung auf Deutsch (max. 10 Wörter).",
                 [{"text": f"Titel: {title}\nInhalt: {'; '.join(o['was'] for o in order)[:1500]}\n\nTrend-Songs:\n" +
                   "\n".join(f"{i}. {x['song']} – {x['artist']} [{x.get('chart')}] {x.get('mood', '')}" for i, x in enumerate(songs))}],
                 {"type": "object", "properties": {"songs": {"type": "array", "items": {"type": "object", "properties": {
                     "nr": {"type": "integer"}, "warum": {"type": "string"}}, "required": ["nr", "warum"]}}}, "required": ["songs"]}, timeout=60)
    out = []
    for x in (res or {}).get("songs") or []:
        n = x.get("nr")
        if isinstance(n, int) and 0 <= n < len(songs) and all(o["song"] != songs[n]["song"] for o in out):
            out.append({"song": songs[n]["song"], "artist": songs[n]["artist"], "warum": str(x.get("warum") or "")[:80]})
    return out[:3]


songs = suggest_songs()
STATS.update(titel=title, sekunden=round(total, 1), bilder=len(order), plan=[o["s"]["id"] for o in order])
joblog = os.environ.get("VELOX_JOBLOG", os.path.join(MASCHINE, "logs", "jobs.jsonl"))
try:
    with open(joblog, "a") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), **STATS}, ensure_ascii=False) + "\n")
except OSError:
    pass
# Beschreibungstext + Hashtags zum Posten (wie pipeline.py)
post = None
res = gemini("Du bist Social-Media-Manager für Unternehmen. Schreibe den Beschreibungstext für diesen Kurzvideo-Post (B-Roll auf "
             "Musik, niemand spricht) auf Deutsch: 1–2 kurze, appetitmachende Sätze und ein kurzer Aufruf (Vorbeikommen, "
             "Kommentieren, Folgen). Höchstens 2 Emojis, nichts erfinden (keine Preise/Orte, die nicht vorkommen). Dazu 5–8 "
             "Hashtags ohne Leerzeichen: 3–4 zur Nische/zum Produkt, 1–2 breite (z. B. #foodtok)."
             + (f" Schreibweise von Namen/Marken: {A.words}." if A.words else ""),
             [{"text": f"Titel: {title}\nZu sehen: {'; '.join(o['was'] for o in order)[:1500]}"}],
             {"type": "object", "properties": {"text": {"type": "string"}, "hashtags": {"type": "array", "items": {"type": "string"}}},
              "required": ["text", "hashtags"]}, timeout=60)
if res and (res.get("text") or "").strip():
    tags = []
    for h in res.get("hashtags") or []:
        h = "#" + re.sub(r"[^\wäöüÄÖÜß]", "", str(h))
        if len(h) > 2 and h.lower() not in (t.lower() for t in tags):
            tags.append(h)
    post = {"text": res["text"].strip()[:600], "hashtags": tags[:8]}
print(json.dumps({"clips": [{"file": final, "title": title or "Musik-Schnitt", "seconds": round(total), "songs": songs, "post": post}]}), flush=True)
