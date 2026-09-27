"""Velox Cut – Schnitt-Pipeline (Clean / Viral).

Ablauf pro Auftrag:
  1. Ton extrahieren, Wort-genau transkribieren (mlx-whisper, lokal auf dem Mac)
  2. Stille im Ton messen (ffmpeg silencedetect)
  3. Schnittliste: Füllwörter (ähm, äh …), Pausen, Wiederholungen/Versprecher raus – Schnitte nur zwischen Wörtern
     • kurzes Video (Rohmaterial)  → ein fertiger Clip aus dem ganzen Video
     • langes Video (Clipping)     → die besten Momente (Gemini, sonst Satz-genaue Abschnitte), je Clip max. 60 s,
                                     Anfang und Ende immer auf Satzgrenzen
  4. Bildausschnitt 9:16 aufs Gesicht, Clean: sanfter Zoom-Wechsel an Schnitten, Viral: Zoom-Punches + Hook-Titel
  5. Untertitel im gewählten Stil + Farbe (ASS, Wort für Wort), Lautheit -14 LUFS, web-sicheres MP4

Ausgabe: Zeilen „PROGRESS n“, am Ende eine JSON-Zeile {"clips": [{"file", "title", "seconds"}]}.
Umgebung: GEMINI_API_KEY (optional), VELOX_FONTS (Ordner mit TTF/OTF), VELOX_WHISPER (Modell).
"""
import argparse, json, os, re, subprocess, sys, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--src", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--style", default="clean", choices=["clean", "viral"])
ap.add_argument("--clips", type=int, default=1)
ap.add_argument("--caption", default="rise")
ap.add_argument("--color", default="#F6E27F")
ap.add_argument("--keep-work", action="store_true", help="Zwischendateien behalten (Fehlersuche)")
A = ap.parse_args()

FONTS = os.environ.get("VELOX_FONTS", "/Volumes/PortableSSD/velox-worker/fonts")
WHISPER = os.environ.get("VELOX_WHISPER", "mlx-community/whisper-large-v3-turbo")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
os.makedirs(A.out, exist_ok=True)


def progress(p):
    print(f"PROGRESS {int(p)}", flush=True)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().splitlines()
        raise RuntimeError(msg[-1] if msg else f"{cmd[0]} Fehler")
    return r


# ─────────────────────────────── 1. Video lesen ───────────────────────────────
def probe(path):
    d = json.loads(run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate:stream_side_data=rotation:format=duration", "-of", "json", path]).stdout)
    s = d["streams"][0]
    w, h = int(s["width"]), int(s["height"])
    rot = next((int(sd["rotation"]) for sd in (s.get("side_data_list") or []) if "rotation" in sd), 0)
    if abs(rot) in (90, 270):
        w, h = h, w
    num, den = (s.get("r_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den or 1)
    audio = json.loads(run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "json", path]).stdout)
    return w, h, fps if 10 < fps < 121 else 30.0, float(d["format"]["duration"]), bool(audio.get("streams"))


W, H, FPS, DUR, HAS_AUDIO = probe(A.src)
if not HAS_AUDIO:
    sys.exit("Das Video hat keine Tonspur – für den automatischen Schnitt brauchen wir Sprache.")
log(f"Quelle {W}x{H} @ {FPS:.2f} fps, {DUR:.1f} s")
progress(12)

# ─────────────────────────────── 2. Transkript ───────────────────────────────
wav = os.path.join(A.out, "audio.wav")
run(["ffmpeg", "-v", "error", "-y", "-i", A.src, "-vn", "-ac", "1", "-ar", "16000", wav])

import mlx_whisper  # noqa: E402  (erst hier laden – dauert)

tr = mlx_whisper.transcribe(wav, path_or_hf_repo=WHISPER, word_timestamps=True, condition_on_previous_text=False,
                            initial_prompt="Ähm, also, äh, ich wollte … ähm, ja. Genau.")
WORDS = []
for seg in tr["segments"]:
    for w in seg.get("words", []):
        t = w["word"].strip()
        if t:
            WORDS.append({"t": t, "s": float(w["start"]), "e": float(w["end"])})
if len(WORDS) < 3:
    sys.exit("Im Video wurde keine Sprache erkannt.")
for i, w in enumerate(WORDS):
    w["i"] = i
log(f"{len(WORDS)} Wörter, Sprache {tr.get('language')}")
progress(35)

# ─────────────────────────────── 3. Stille messen ───────────────────────────────
LV = []  # Lautstärke (dB) in 50-ms-Schritten


def valley(t0, t1):
    """Leisester Moment zwischen t0 und t1 – dort klingt ein Schnitt am natürlichsten."""
    i0, i1 = max(0, int(t0 / 0.05)), min(len(LV) - 1, int(t1 / 0.05))
    if i1 <= i0:
        return (t0 + t1) / 2
    i = min(range(i0, i1 + 1), key=lambda k: LV[k])
    return i * 0.05 + 0.025


def measure_silence():
    """Stille selbst messen: Lautstärke in 50-ms-Schritten, Schwelle je Video knapp über dem Grundrauschen.
    (ffmpeg-silencedetect scheitert an Raum-/Mikrofonrauschen – dann würde keine einzige Pause erkannt.)"""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", wav, "-af",
                        "asetnsamples=800,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
                        "-f", "null", "-"], capture_output=True, text=True)
    lv = [(-120.0 if m == "-inf" else float(m)) for m in re.findall(r"RMS_level=(-?inf|-?[\d.]+)", r.stderr)]
    LV[:] = lv
    if len(lv) < 20:
        return -34.0, []
    srt = sorted(lv)
    floor, speech = srt[len(srt) // 10], srt[len(srt) * 6 // 10]
    th = min(-18.0, max(-60.0, floor + max(4.0, (speech - floor) * 0.3)))
    sil, start = [], None
    for i, v in enumerate(lv + [0.0]):
        if v < th and start is None:
            start = i
        elif v >= th and start is not None:
            if (i - start) * 0.05 >= 0.2:
                sil.append((start * 0.05, min(DUR, i * 0.05)))
            start = None
    return round(th, 1), sil


NOISE_DB, SIL = measure_silence()
log(f"Stille-Schwelle {NOISE_DB} dB, {len(SIL)} Pausen gefunden")

# ─────────────────────────────── 4. Was fliegt raus? ───────────────────────────────
def norm(s):
    return re.sub(r"[^\wäöüß]", "", s.lower())


FILLER = {"äh", "ähm", "ähh", "ähmm", "öh", "öhm", "hm", "hmm", "mhm", "ehm", "eh", "uh", "um", "uhm", "erm"}
SENT_END = re.compile(r"[.!?…]$")


def filler_idx(words):
    out = set()
    for k, w in enumerate(words):
        n = norm(w["t"])
        if n in FILLER:
            out.add(w["i"])
        # „Genau.“ / „Okay.“ als eigener Mini-Satz ganz am Anfang oder Ende = Füllwort
        solo = SENT_END.search(w["t"]) and (k == 0 or SENT_END.search(words[k - 1]["t"]))
        if n in {"genau", "also", "okay", "ok", "so"} and solo and k in (0, len(words) - 1):
            out.add(w["i"])
    return out


def repeat_idx(words):
    """Versprecher/Neustarts: „ich hab ich habe“, „der der“, „wir sind wir sind gerade“ → erste Fassung raus."""
    out, toks, n = set(), [norm(w["t"]) for w in words], len(words)
    for k in range(n):
        for L in (4, 3, 2, 1):
            if k + 2 * L > n:
                continue
            a, b = toks[k:k + L], toks[k + L:k + 2 * L]
            if not all(a):
                continue
            same = a == b or (L >= 2 and a[:-1] == b[:-1] and len(a[-1]) >= 2 and b[-1].startswith(a[-1][:2]))
            if same and not SENT_END.search(words[k + L - 1]["t"]):
                out.update(words[j]["i"] for j in range(k, k + L))
                break
    return out


def gemini(system, user, schema):
    """Gemini (optional). None, wenn kein Schlüssel oder Fehler – dann gelten nur die Regeln."""
    if not GEMINI_KEY:
        return None
    body = json.dumps({"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}],
                       "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json", "responseSchema": schema}}).encode()
    for model in ("gemini-3-flash-preview", "gemini-flash-latest", "gemini-2.5-flash"):
        try:
            req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", data=body,
                                         headers={"x-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                d = json.loads(resp.read())
            return json.loads("".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"]))
        except Exception as e:  # noqa: BLE001
            log(f"Gemini {model}: {e}")
    return None


def gemini_cuts(words):
    """KI prüft den Text auf Versprecher, abgebrochene Sätze und Füllsätze (ergänzt die Regeln)."""
    res = gemini(
        "Du bist ein Profi-Cutter für Kurzvideos. Du bekommst das Wort-Transkript eines gesprochenen Videos, jedes Wort mit [Nummer]. "
        "Markiere Wörter, die für einen sauberen, flüssigen Schnitt entfernt werden sollen: Füllwörter (äh, ähm), Versprecher, "
        "abgebrochene Satzanfänge, doppelt gesagte Satzteile (die erste/schwächere Fassung), Selbstkorrekturen, überflüssiges „genau“ am Ende. "
        "Der Rest muss grammatisch korrekt und inhaltlich vollständig bleiben. Nichts Inhaltliches entfernen. Im Zweifel behalten.",
        " ".join(f"[{w['i']}]{w['t']}" for w in words),
        {"type": "object", "properties": {"remove": {"type": "array", "items": {"type": "integer"}}}, "required": ["remove"]})
    return set(res["remove"]) if res and isinstance(res.get("remove"), list) else set()


def gemini_moments(words, n):
    """KI wählt die n stärksten, in sich geschlossenen Momente (20–60 s) – Start/Ende auf Satzgrenzen."""
    res = gemini(
        "Du bist ein viraler Short-Form-Editor (TikTok/Reels/Shorts). Wähle aus dem Transkript die stärksten Momente als eigenständige Clips. "
        "Jeder Clip: 20–60 Sekunden, beginnt mit einem starken Hook-Satz (Aussage, Zahl, Frage, Konflikt), ist ohne Kontext verständlich "
        "und endet erst, wenn der Gedanke abgeschlossen ist (nie mitten im Satz). Gib Start- und End-Wortnummer an (inklusive) und einen "
        "kurzen, packenden Titel (max. 6 Wörter, Sprache des Videos). Clips dürfen sich nicht überschneiden. Sortiere nach Viralität.",
        f"Anzahl Clips: {n}\n\n" + " ".join(f"[{w['i']}|{w['s']:.0f}s]{w['t']}" for w in words),
        {"type": "object", "properties": {"clips": {"type": "array", "items": {"type": "object", "properties": {
            "start": {"type": "integer"}, "end": {"type": "integer"}, "title": {"type": "string"}},
            "required": ["start", "end", "title"]}}}, "required": ["clips"]})
    return res.get("clips") if res else None


def sentences(words):
    out, cur = [], []
    for w in words:
        cur.append(w)
        if SENT_END.search(w["t"]):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def rule_moments(words, n):
    """Ohne KI: Satz-genaue Abschnitte von 25–58 s, dichteste Sprache zuerst."""
    sents, cands, i = sentences(words), [], 0
    while i < len(sents):
        j, s0 = i, sents[i][0]["s"]
        while j < len(sents) and sents[j][-1]["e"] - s0 < 25:
            j += 1
        if j >= len(sents):
            break
        while j + 1 < len(sents) and sents[j + 1][-1]["e"] - s0 <= 58:
            j += 1
        seg = [w for s in sents[i:j + 1] for w in s]
        cands.append((len(seg) / max(1, seg[-1]["e"] - seg[0]["s"]), seg[0]["i"], seg[-1]["i"]))
        i = j + 1
    picked = sorted(sorted(cands, reverse=True)[:n], key=lambda c: c[1])
    return [{"start": a, "end": b, "title": ""} for _, a, b in picked] or [{"start": 0, "end": len(words) - 1, "title": ""}]


SHORT = DUR <= 150  # Rohmaterial → ein Clip aus dem Ganzen
if SHORT:
    MOMENTS = [{"start": 0, "end": len(WORDS) - 1, "title": ""}]
else:
    MOMENTS = [m for m in (gemini_moments(WORDS, A.clips) or []) if 0 <= m["start"] <= m["end"] < len(WORDS)][:A.clips]
    MOMENTS = MOMENTS or rule_moments(WORDS, A.clips)
progress(45)


STATS = {}


def syllables(t):
    return max(1, len(re.findall(r"[aeiouyäöü]+", t.lower())))


def expected_len(t):
    return 0.16 * syllables(t) + 0.12


def _clip_wav(a, b, path):
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, a):.3f}", "-to", f"{b:.3f}", "-i", wav, path])


def _looks_hallucinated(ws):
    toks = [norm(w["word"]) for w in ws]
    zero = sum(1 for w in ws if w["end"] - w["start"] <= 0.001)
    if len(ws) > 30 or (len(ws) > 6 and zero > len(ws) * 0.3):
        return True
    return any(toks[i] and toks[i] == toks[i + 2] == toks[i + 4] for i in range(len(toks) - 4))


def cut_score(w, a, b):
    """Stelle (mit Kontext) ohne [a, b] neu abhören. 0 = Wort nicht mehr erkennbar, sonst Erkennungs-Sicherheit (0..1)."""
    x0, x1 = max(0.0, w["s"] - 0.8), min(DUR, w["e"] + 0.8)
    tmp = os.path.join(A.out, "check.wav")
    run(["ffmpeg", "-v", "error", "-y", "-i", wav, "-af",
         f"aselect='between(t,{x0:.3f},{a:.3f})+between(t,{b:.3f},{x1:.3f})',asetpts=N/SR/TB", tmp])
    try:
        r = mlx_whisper.transcribe(tmp, path_or_hf_repo=WHISPER, word_timestamps=True, temperature=0.0,
                                   condition_on_previous_text=False)
    except Exception:  # noqa: BLE001
        return 0.0
    target = norm(w["t"])
    probs = [x.get("probability", 0.0) for sg in r["segments"] for x in sg.get("words", []) if norm(x["word"]) == target]
    return max(probs) if probs else 0.0


def cut_still_clear(w, a, b):
    return cut_score(w, a, b) >= 0.5


def hidden_cuts(words):
    """Whisper „schluckt“ Füllwörter gern in Nachbarwörter (ein 1,4 s langes „Restaurants“ = „ähm Restaurants“)
    und dehnt gezogene Wörter („uuund“). Verdächtig lange Wörter werden mit Kontext neu abgehört:
    gefundene Füllwörter werden exakt geschnitten, gedehnte Wörter auf normale Länge gekürzt."""
    cuts, found, trimmed = [], 0, 0
    snip = os.path.join(A.out, "snip.wav")
    for k, w in enumerate(words):
        dur, exp = w["e"] - w["s"], expected_len(w["t"])
        if dur < exp + 0.35:
            continue
        a, b = max(0.0, w["s"] - 1.2), min(DUR, w["e"] + 1.2)
        _clip_wav(a, b, snip)
        try:
            r = mlx_whisper.transcribe(snip, path_or_hf_repo=WHISPER, word_timestamps=True, temperature=0.0,
                                       condition_on_previous_text=False, initial_prompt="Äh, ähm, also.")
            ws = [x for sg in r["segments"] for x in sg.get("words", [])]
        except Exception:  # noqa: BLE001
            ws = []
        hit = False
        if ws and not _looks_hallucinated(ws):
            for x in ws:
                xs, xe = a + x["start"], a + x["end"]
                mid = (xs + xe) / 2
                if norm(x["word"]) in FILLER and w["s"] - 0.1 <= mid <= w["e"] + 0.1 and xe - xs > 0.05:
                    if cut_still_clear(w, xs - 0.02, xe + 0.03):
                        cuts.append((xs - 0.02, xe + 0.03))
                        hit = True
        if hit:
            found += 1
            continue
        if dur > exp + 0.45:
            # gedehnt oder verstecktes „ähm“ davor/danach: Anfang, Mitte oder Ende kürzen – die Variante,
            # bei der das Wort danach am klarsten erkannt wird, gewinnt (sonst bleibt alles, wie es ist)
            extra = dur - exp
            options = [(w["s"], w["s"] + extra), (w["e"] - extra, w["e"]), (w["s"] + exp * 0.4, w["e"] - exp * 0.6)]
            scored = [(cut_score(w, x, y), (x, y)) for x, y in options if y - x > 0.2]
            best = max(scored, default=(0.0, None))
            if best[0] >= 0.5:
                cuts.append(best[1])
                trimmed += 1
    STATS.update(hidden_fillers=found, stretched=trimmed)
    return cuts


def subtract(ranges, cuts):
    out = []
    for s, e in ranges:
        segs = [[s, e]]
        for a, b in sorted(cuts):
            nxt = []
            for x, y in segs:
                if b <= x or a >= y:
                    nxt.append([x, y])
                    continue
                if a > x + 0.04:
                    nxt.append([x, a])
                if b < y - 0.04:
                    nxt.append([b, y])
            segs = nxt
        out.extend(segs)
    return [r for r in out if r[1] - r[0] > 0.06]


def keep_ranges(words, safe=False):
    """Zeitbereiche, die bleiben: ohne Füllwörter/Wiederholungen, Stille raus, Schnitte nur zwischen Wörtern."""
    fil, rep, gem = filler_idx(words), repeat_idx(words), gemini_cuts(words)
    drop = fil | rep | gem
    STATS.update(filler=len(fil), repeats=len(rep), gemini=len(gem), gemini_on=bool(GEMINI_KEY),
                 removed=[w["t"] for w in words if w["i"] in drop][:80])
    kept = [w for w in words if w["i"] not in drop]
    PAD_IN, PAD_OUT, MAX_GAP = (0.08, 0.2, 0.2) if safe else (0.05, 0.1, 0.14)
    ranges = []
    for w in kept:
        tail = PAD_OUT + (0.08 if SENT_END.search(w["t"]) or w["t"].endswith(",") else 0)  # Satzende darf ausklingen
        s, e = max(0.0, w["s"] - PAD_IN), min(DUR, w["e"] + tail)
        need = min(w["e"] - w["s"], expected_len(w["t"]))  # jedes Wort behält mindestens seine normale Länge
        for a, b in ([] if safe else SIL):  # Whisper dehnt Wörter gern über Pausen – Stille am Rand abschneiden
            if a <= s + 0.04 < b < e:
                s = min(b - PAD_IN, w["e"] - need - PAD_IN)
            if s < a < e - 0.04 <= b:
                e = max(a + tail, w["s"] + need + tail)
        if e - s < 0.08:
            continue
        if ranges and s - ranges[-1][1] <= MAX_GAP:
            ranges[-1][1] = max(ranges[-1][1], e)
        else:
            ranges.append([s, e])
    # Lücken zwischen Wörtern nur dort schneiden, wo WIRKLICH Stille ist (oder ein entferntes Füllwort) –
    # Whisper-Zeitstempel verrutschen, in „Lücken“ steckt oft noch gesprochener Text („… mit dabei“)
    dropped = []
    for k, w in enumerate(words):
        if w["i"] not in drop:
            continue
        a, b = w["s"] - 0.03, w["e"] + 0.03
        if w["i"] in fil:  # Füllwort: bis an die umgebenden Pausen ausdehnen (Whisper setzt „ähm“ oft zu kurz an)
            prev_e = max((x["e"] for x in words[:k] if x["i"] not in drop), default=0.0) + 0.05
            next_s = min((x["s"] for x in words[k + 1:] if x["i"] not in drop), default=DUR) - 0.05
            before = [y for x, y in SIL if w["s"] - 0.5 <= y <= w["s"] + 0.15]
            after = [x for x, y in SIL if w["e"] - 0.15 <= x <= w["e"] + 0.5]
            # Grenzen an die Pause bzw. an die Lautstärke-Senke legen (Whisper-Zeiten verrutschen oft um 0,2–0,3 s)
            a = max(prev_e, max(before) - 0.02) if before else max(prev_e - 0.05, valley(w["s"] - 0.45, w["s"] + 0.1))
            b = min(next_s + 0.3, min(after) + 0.02) if after else valley(w["e"] - 0.05, min(w["e"] + 0.5, next_s + 0.3))
        dropped.append((a, b))
    filled = []
    for k, (s, e) in enumerate(ranges):
        if filled:
            gap_a, gap_b = filled[-1][1], s
            cuts_in_gap = sorted([(max(a, gap_a), min(b, gap_b)) for a, b in SIL + dropped if b > gap_a and a < gap_b])
            pos, keep_parts = gap_a, []
            for a, b in cuts_in_gap:
                if a - pos > 0.06:
                    keep_parts.append([pos, a + 0.04])
                pos = max(pos, b - 0.04)
            if gap_b - pos > 0.06:
                keep_parts.append([pos, gap_b])
            if not cuts_in_gap:  # gar keine Stille gemessen → alles behalten
                filled[-1][1] = e
                continue
            for x, y in keep_parts:
                if x - filled[-1][1] <= MAX_GAP:
                    filled[-1][1] = y
                else:
                    filled.append([x, y])
            if s - filled[-1][1] <= MAX_GAP:
                filled[-1][1] = e
                continue
        filled.append([s, e])
    ranges = filled
    out = []  # lange Stille mitten in einem Block (gedehnte Wörter „uuund …“) ebenfalls raus
    for s, e in ranges:
        cur = s
        for a, b in ([] if safe else SIL):
            if a > cur + 0.1 and b < e - 0.1 and b - a > 0.3:
                out.append([cur, a + PAD_OUT])
                cur = b - PAD_IN
        out.append([cur, e])
    out = [r for r in out if r[1] - r[0] > 0.08]
    if not safe:
        out = subtract(out, hidden_cuts(kept))
    return out, kept


# ─────────────────────────────── 5. Bildausschnitt (Gesicht) ───────────────────────────────
import cv2  # noqa: E402

CAPV = cv2.VideoCapture(A.src)
FACE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def face_center(t0, t1):
    pts = []
    for f in (0.25, 0.5, 0.75):
        CAPV.set(cv2.CAP_PROP_POS_MSEC, (t0 + (t1 - t0) * f) * 1000)
        ok, fr = CAPV.read()
        if not ok:
            continue
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        sc = 480 / max(g.shape)
        g = cv2.resize(g, None, fx=sc, fy=sc)
        fs = FACE.detectMultiScale(g, 1.1, 5, minSize=(int(min(g.shape) * .1),) * 2)
        if len(fs):
            x, y, w, h = max(fs, key=lambda f: f[2] * f[3])
            pts.append(((x + w / 2) / g.shape[1], (y + h / 2) / g.shape[0], h / g.shape[0]))
    if not pts:
        return None
    return sorted(pts)[len(pts) // 2]


BW, BH = (H * 9 / 16, H) if W / H > 9 / 16 else (W, W * 16 / 9)  # größter 9:16-Ausschnitt


def measure_luma():
    vals = []
    for f in (0.2, 0.5, 0.8):
        r = subprocess.run(["ffmpeg", "-v", "info", "-ss", f"{DUR * f:.2f}", "-i", A.src, "-frames:v", "1", "-vf",
                            "scale=320:-2,signalstats,metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-"],
                           capture_output=True, text=True)
        m = re.search(r"YAVG=([\d.]+)", r.stderr)
        if m:
            vals.append(float(m.group(1)))
    return sum(vals) / len(vals) if vals else 110.0


LUMA = measure_luma()
# sanfte Korrektur: dunkle Handyvideos aufhellen, immer leicht mehr Farbe/Kontrast
if LUMA < 60:
    GRADE = "eq=gamma=1.38:contrast=1.07:saturation=1.12"
elif LUMA < 85:
    GRADE = "eq=gamma=1.2:contrast=1.05:saturation=1.1"
else:
    GRADE = "eq=contrast=1.03:saturation=1.07"
log(f"Helligkeit {LUMA:.0f} → {GRADE}")


FACES_OUT = []  # Gesichtslage im fertigen Bild (oben, unten in px) – für die Position der Untertitel


def crop_for(t0, t1, zoom, last):
    found = face_center(t0, t1)
    c = found or last or (0.5, 0.4, 0.0)
    cw, ch = BW / zoom, BH / zoom
    x = min(max(c[0] * W - cw / 2, 0), W - cw)
    y = min(max(c[1] * H - ch * 0.36, 0), H - ch)  # Gesicht eher oben, unten Platz für Untertitel
    if found:
        cy, fh = (c[1] * H - y) / ch * 1920, c[2] * H / ch * 1920
        FACES_OUT.append((cy - fh * 0.75, cy + fh * 0.85))  # Stirn bis Kinn
    ev = lambda v: int(v) // 2 * 2
    return f"crop={ev(cw)}:{ev(ch)}:{ev(x)}:{ev(y)},scale=1080:1920:flags=lanczos,setsar=1,{GRADE}", c


# ─────────────────────────────── 6. Untertitel (ASS) ───────────────────────────────
def ass_color(hex_):
    h = hex_.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


ACC, WHITE, BLACK = ass_color(A.color), "&H00FFFFFF", "&H00000000"
# wie die Vorschau auf der Website: Schrift, Größe, Kontur, Wörter pro Einblendung
CAP = {
    "rise":    dict(font="Poppins SemiBold", size=150, bold=0, ital=0, bord=0, shad=5, back="&H80000000", n=4, chars=16, case="lower", pop=False, blur=0),
    "clean":   dict(font="Montserrat", size=138, bold=1, ital=0, bord=10, shad=0, back=BLACK, n=4, chars=14, case=None, pop=True, blur=0),
    "hormozi": dict(font="Montserrat Thin Black", size=160, bold=0, ital=0, bord=13, shad=6, back=BLACK, n=3, chars=11, case="upper", pop=True, blur=0),
    "neon":    dict(font="Bebas Neue", size=230, bold=0, ital=0, bord=5, shad=0, back=BLACK, n=3, chars=14, case="upper", pop=False, blur=2, glow=True),
    "comic":   dict(font="Bangers", size=220, bold=0, ital=0, bord=12, shad=8, back=BLACK, n=2, chars=10, case="upper", pop=True, blur=0),
    "serif":   dict(font="Playfair Display", size=158, bold=1, ital=1, bord=0, shad=5, back="&H80000000", n=4, chars=16, case=None, pop=False, blur=0),
}
CS = CAP.get(A.caption, CAP["rise"])


def is_emph(t):
    c = norm(t)
    return bool(re.search(r"\d", t)) or (len(c) >= 9 and t[:1].isupper()) or c in {"nie", "immer", "niemand", "alles", "nichts", "krass"}


def ass_time(t):
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def fmt_word(t):
    t = t.replace("{", "(").replace("}", ")")
    if CS["case"] == "upper":
        t = t.upper()
    elif CS["case"] == "lower":
        t = t.lower()
    return re.sub(r"[.,;:]+$", "", t) if CS["case"] else t


def caption_y():
    """Untertitel standardmäßig im unteren Drittel – liegt dort das Gesicht, darüber bzw. darunter ausweichen."""
    y, half = 1390, CS["size"] * 0.6
    if not FACES_OUT:
        return y
    top = sorted(f[0] for f in FACES_OUT)[len(FACES_OUT) // 2]
    bot = sorted(f[1] for f in FACES_OUT)[len(FACES_OUT) // 2]
    if bot < y - half or top > y + half:
        return y
    if bot + half + 40 < 1740:  # unter dem Kinn ist Platz
        return int(bot + half + 40)
    return int(max(330, top - half - 60))  # sonst über den Kopf


def build_ass(words_out, path, hook=None):
    """words_out: [(text, start, end)] in Ausgabe-Zeit. Wörter erscheinen nacheinander, das gesprochene in Akzentfarbe."""
    head = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{CS['font']},{CS['size']},{WHITE},{ACC},{ACC if CS.get('glow') else BLACK},{CS['back']},{CS['bold']},{CS['ital']},0,0,"
        f"100,100,1,0,1,{CS['bord']},{CS['shad']},5,70,70,0,1",
        f"Style: Hook,Montserrat Thin Black,70,{BLACK},{ACC},{WHITE},&H00000000,0,0,0,0,100,100,0,0,3,20,0,8,90,90,200,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    pos = "{\\an5\\pos(540," + str(caption_y()) + ")" + (f"\\blur{CS['blur']}" if CS["blur"] else "") + "}"
    groups, cur = [], []
    for w in words_out:
        cur.append(w)
        if len(cur) >= CS["n"] or re.search(r"[.!?,;:…]$", w[0]) or sum(len(x[0]) + 1 for x in cur) > CS["chars"]:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    ev = []
    for gi, g in enumerate(groups):
        nxt = groups[gi + 1][0][1] if gi + 1 < len(groups) else None
        g_end = nxt if nxt is not None and nxt - g[-1][2] < 0.7 else g[-1][2] + 0.3
        for k, (_, s, _) in enumerate(g):
            e_show = g[k + 1][1] if k + 1 < len(g) else g_end
            parts = []
            for j, (tj, _, _) in enumerate(g):
                wtxt = fmt_word(tj)
                if j > k:  # kommt gleich – schon sichtbar (Zeile bleibt mittig), bei weichen Stilen leicht gedimmt
                    dim = A.caption in ("rise", "serif")
                    parts.append(("{\\alpha&H55&}" if dim else "") + wtxt + ("{\\alpha&H00&}" if dim else ""))
                elif j == k:
                    tag = "\\1c" + ACC
                    if CS["pop"]:
                        tag += "\\fscx70\\fscy70\\t(0,90,\\fscx112\\fscy112)\\t(90,170,\\fscx103\\fscy103)"
                    else:
                        tag += "\\fscy88\\t(0,120,\\fscy100)"
                    parts.append("{" + tag + "}" + wtxt + "{\\r}")
                elif is_emph(tj) and A.caption in ("rise", "neon", "comic", "serif"):
                    parts.append("{\\1c" + ACC + "}" + wtxt + "{\\r}")
                else:
                    parts.append(wtxt)
            ev.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e_show)},Cap,,0,0,0,,{pos}{' '.join(parts)}")
    if hook:
        ev.append(f"Dialogue: 1,{ass_time(0)},{ass_time(3.2)},Hook,,0,0,0,,{{\\fad(120,200)}}{hook.upper()}")
    open(path, "w", encoding="utf-8").write("\n".join(head + ev) + "\n")


# ─────────────────────────────── 7. Rendern ───────────────────────────────
def render(idx, ranges, kept, title):
    out = os.path.join(A.out, f"clip-{idx}.mp4")
    ass = os.path.join(A.out, f"clip-{idx}.ass")
    viral = A.style == "viral"
    offs, t_out = [], 0.0
    for s, e in ranges:
        offs.append((s, e, t_out))
        t_out += e - s
    words_out = []
    for w in kept:
        for s, e, o in offs:
            if s - 0.03 <= w["s"] < e:
                ws = o + max(0.0, w["s"] - s)
                words_out.append((w["t"], ws, max(o + min(e, w["e"]) - s, ws + 0.08)))
                break
    FACES_OUT.clear()
    fc, parts, last = [], [], None
    for k, (s, e) in enumerate(ranges):
        jump = k > 0 and s - ranges[k - 1][1] > 0.2  # echter Sprung-Schnitt → Zoomwechsel kaschiert ihn
        zoom = [1.0, 1.16, 1.06, 1.22][k % 4] if viral else (1.07 if jump and k % 2 else 1.0)
        crop, last = crop_for(s, e, zoom, last)
        fade = min(0.012, (e - s) / 4)
        fc.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS,{crop},fps={min(FPS, 60):.3f}[v{k}]")
        fc.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS,afade=t=in:d={fade:.3f},"
                  f"afade=t=out:st={max(0.0, e - s - fade):.3f}:d={fade:.3f}[a{k}]")
        parts.append(f"[v{k}][a{k}]")
    build_ass(words_out, ass, title if viral else None)  # nach dem Zuschnitt: kennt jetzt die Gesichtslage
    STATS["caption_words"] = len(words_out)
    fc.append(f"{''.join(parts)}concat=n={len(ranges)}:v=1:a=1[vc][ac]")
    esc = lambda p: p.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    fc.append(f"[vc]ass='{esc(ass)}':fontsdir='{esc(FONTS)}',format=yuv420p[vo]")
    fc.append("[ac]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[ao]")
    script = os.path.join(A.out, f"clip-{idx}.filter")
    open(script, "w").write(";\n".join(fc))
    run(["ffmpeg", "-v", "error", "-y", "-i", A.src, "-/filter_complex", script, "-map", "[vo]", "-map", "[ao]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-profile:v", "high", "-level", "4.1", "-color_range", "tv",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out])
    if not A.keep_work:
        for f in (ass, script):
            os.remove(f)
    return out, round(t_out, 1)


def missing_words(path, kept):
    """Hörprobe: fertigen Clip neu transkribieren und prüfen, ob alle behaltenen Wörter noch zu hören sind."""
    tmp = os.path.join(A.out, "hoerprobe.wav")
    run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vn", "-ac", "1", "-ar", "16000", tmp])
    r = mlx_whisper.transcribe(tmp, path_or_hf_repo=WHISPER, condition_on_previous_text=False, temperature=0.0)
    os.remove(tmp)
    import difflib
    stem = lambda t: t[:5]
    toks = lambda text: [stem(x) for x in re.split(r"[\s\-–]+", text.lower()) if norm(x)]
    heard = [stem(norm(x)) for x in toks(r.get("text", ""))]
    want, owner = [], []
    for w in kept:  # Wörter mit Bindestrich in Teile zerlegen, damit „Baden -Württemberg“ = „Baden-Württemberg“
        for part in re.split(r"[\-–]+", w["t"]):
            if norm(part):
                want.append(stem(norm(part)))
                owner.append(w["t"])
    STATS["fillers_heard"] = len(re.findall(r"\b(ähm|äh|öhm|ähh)\b", r.get("text", "").lower()))
    sm = difflib.SequenceMatcher(a=want, b=heard, autojunk=False)
    hit = set()
    for blk in sm.get_matching_blocks():
        hit.update(range(blk.a, blk.a + blk.size))
    miss = []
    for i, o in enumerate(owner):
        if i not in hit and (not miss or miss[-1] != o):
            miss.append(o)
    return miss


LOG = os.environ.get("VELOX_JOBLOG", "/Volumes/PortableSSD/velox-worker/logs/jobs.jsonl")


def quality_check(path, words, kept, orig, secs):
    """Sicherheits-Check: Ein Clip wird nur ausgeliefert, wenn wirklich geschnitten und untertitelt wurde.
    Schlägt er fehl, bricht die Pipeline ab → Auftrag „fehlgeschlagen“, Credits werden automatisch erstattet."""
    t0, t1 = words[0]["s"], words[-1]["e"]
    pauses = sum(min(b, t1) - max(a, t0) for a, b in SIL if b > t0 and a < t1 and b - a > 0.4)
    removable = pauses + 0.3 * STATS.get("filler", 0)
    removed = orig - secs
    problems = []
    if kept and STATS.get("caption_words", 0) < len(kept) * 0.9:
        problems.append("Untertitel fehlen")
    if removable > 1.2 and removed < min(0.5, removable * 0.3):
        problems.append(f"Schnitt hat nicht gegriffen ({removable:.1f} s Pausen/Füller erkannt, nur {removed:.1f} s entfernt)")
    info = json.loads(run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration", "-of", "json", path]).stdout)
    kinds = {st["codec_type"] for st in info["streams"]}
    vid = next((st for st in info["streams"] if st["codec_type"] == "video"), {})
    if kinds != {"video", "audio"} or (vid.get("width"), vid.get("height")) != (1080, 1920):
        problems.append("Ausgabedatei unvollständig")
    if abs(float(info["format"]["duration"]) - secs) > 1.0:
        problems.append("Länge stimmt nicht")
    entry = {"style": A.style, "caption": A.caption, "src_s": round(DUR, 1), "orig_s": round(orig, 1), "out_s": secs,
             "pauses_s": round(pauses, 1), "removed_s": round(removed, 1), **STATS, "problems": problems}
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": __import__("time").strftime("%Y-%m-%d %H:%M"), **entry}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    if problems:
        sys.exit("Qualitäts-Check nicht bestanden: " + "; ".join(problems))


def limit60(ranges, kept):
    """Clips aus langen Videos: max. 60 s nach dem Schnitt – am letzten Satzende davor kürzen."""
    if SHORT or sum(e - s for s, e in ranges) <= 60.5:
        return ranges, kept
    acc, cut_at = 0.0, None
    for s, e in ranges:
        for w in (w for w in kept if s <= w["e"] <= e + 0.1):
            if acc + (w["e"] - s) > 60:
                break
            if SENT_END.search(w["t"]):
                cut_at = w["e"] + 0.12
        acc += e - s
        if acc > 60:
            break
    if not cut_at:
        return ranges, kept
    return [[s, min(e, cut_at)] for s, e in ranges if s < cut_at], [w for w in kept if w["e"] <= cut_at]


results = []
for n, m in enumerate(MOMENTS, 1):
    words = WORDS[m["start"]:m["end"] + 1]
    ranges, kept = keep_ranges(words)
    if not ranges:
        continue
    ranges, kept = limit60(ranges, kept)
    title = (m.get("title") or "").strip()
    if not title:
        first = next((s for s in sentences(kept) if len(s) >= 3), kept[:8])
        title = " ".join(w["t"] for w in first[:7]).rstrip(",.;:")
    f, secs = render(n, ranges, kept, title)
    miss = missing_words(f, kept)
    STATS["missing_first"] = miss[:20]
    if len(miss) > max(1, len(kept) * 0.03):  # Wörter angeschnitten → vorsichtiger neu schneiden
        log(f"Hörprobe: {len(miss)} Wörter unklar ({', '.join(miss[:6])}) – schneide im sicheren Modus neu")
        ranges, kept = limit60(*keep_ranges(words, safe=True))
        f, secs = render(n, ranges, kept, title)
        miss = missing_words(f, kept)
        STATS["safe_mode"] = True
    STATS["missing"] = miss[:20]
    orig = words[-1]["e"] - words[0]["s"]
    log(f"Clip {n}: {orig:.1f} s → {secs} s, {len(ranges)} Abschnitte, {len(words) - len(kept)} Wörter entfernt")
    quality_check(f, words, kept, orig, secs)
    results.append({"file": f, "title": title[:80], "seconds": secs})
    progress(50 + 45 * n / len(MOMENTS))

if not A.keep_work:
    os.remove(wav)
if not results:
    sys.exit("Es konnten keine Clips erstellt werden.")
print(json.dumps({"clips": results}, ensure_ascii=False), flush=True)
