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
import argparse, difflib, json, os, re, subprocess, sys, urllib.error, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--src", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--style", default="clean", choices=["clean", "viral", "premium"])
ap.add_argument("--clips", type=int, default=1)
ap.add_argument("--caption", default="rise")
ap.add_argument("--color", default="#F6E27F")
ap.add_argument("--skip", type=int, default=0, help="so viele der besten Momente überspringen (derselbe Link wurde schon geschnitten → nächstbester Clip)")
ap.add_argument("--keep-work", action="store_true", help="Zwischendateien behalten (Fehlersuche)")
ap.add_argument("--no-review", action="store_true", help="ohne KI-Abnahme (nur für schnelle Tests)")
ap.add_argument("--music", default="none", help="Hintergrundmusik: none | auto (KI wählt passend) | Name aus schnitt-maschine/musik")
ap.add_argument("--logo", default="", help="Logo des Kunden (PNG mit Transparenz) – steht oben links im Video")
ap.add_argument("--plan", default="", help="Schnittplan aus montage.py (mehrere Clips → ein Video): B-Rolls, Bilder, Clip-Grenzen")
ap.add_argument("--words", default="", help="Namen & Marken des Kunden (z. B. „Malo's, Corndogs, Smashburger“) – richtige Schreibweise")
A = ap.parse_args()
PLAN = json.load(open(A.plan)) if A.plan and os.path.exists(A.plan) else None
BRAND = re.sub(r"\s+", " ", A.words).strip()[:300]  # Namen/Marken, die im Video vorkommen (Kunde trägt sie im Studio ein)
LOUD = A.style in ("viral", "premium")  # Viral und Premium: harte Zoom-Schnitte, Punches, Sounds, Hook-Titel
PREM = A.style == "premium"             # Premium zusätzlich: 3D-Titel, Motion Graphics (Schlüsselwörter), Zähler, Kino-Look

# Ordner „schnitt-maschine“ liegt neben „app“ (alles in einem Ordner auf der SSD)
MASCHINE = os.environ.get("VELOX_MASCHINE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "schnitt-maschine"))
FONTS = os.environ.get("VELOX_FONTS", os.path.join(MASCHINE, "fonts"))
WHISPER = os.environ.get("VELOX_WHISPER", "mlx-community/whisper-large-v3-turbo")
import ki  # noqa: E402  gemeinsamer KI-Zugang (worker/ki.py)
GEMINI_KEY = ki.available()
STATS = {}  # Protokoll je Auftrag (jobs.jsonl) – früh anlegen, gemini() schreibt schon beim Story-Schritt hinein
if PLAN and PLAN.get("ki_tokens"):  # KI-Verbrauch der Montage mitzählen (Kosten je Auftrag)
    STATS["ki_tokens"] = {"aufrufe": 0, "ein": 0, "aus": 0, "denken": 0, **{k: int(v) for k, v in PLAN["ki_tokens"].items()}}
os.makedirs(A.out, exist_ok=True)


def progress(p):
    print(f"PROGRESS {int(p)}", flush=True)


_T0 = __import__("time").time()


def stage(text, p=None):
    """Was gerade passiert – erscheint im Studio beim Auftrag. Zeitpunkt je Schritt landet im Protokoll (wo geht Zeit hin?)."""
    print(f"STAGE {text}", flush=True)
    STATS.setdefault("timing", []).append((text[:28], round(__import__("time").time() - _T0)))
    if p is not None:
        progress(p)


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


def work_copy():
    """Große Dateien (4K/3K, HEVC von DJI/iPhone, 50–100 Mbit/s) zuerst in eine schlanke Arbeitskopie umwandeln:
    längste Seite 1920 px (= Endformat, kein Qualitätsverlust im Ergebnis), ca. 12 Mbit/s H.264, über den Grafikchip
    des Macs (VideoToolbox). Alle weiteren Schritte laufen dann auf der kleinen Datei – der ganze Schnitt wird schneller."""
    mbit = os.path.getsize(A.src) * 8 / max(DUR, 1.0) / 1e6
    if max(W, H) <= 1920 and mbit <= 16:
        return None
    stage("Bereitet dein Video vor (große Datei wird verkleinert) …", 6)
    out = os.path.join(A.out, "arbeitskopie.mp4")
    common = ["-map", "0:v:0", "-map", "0:a:0?", "-dn", "-map_metadata", "-1",
              "-vf", f"scale='if(gt(iw,ih),1920,-2)':'if(gt(iw,ih),-2,1920)',fps={min(FPS, 60):.3f}",
              "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    try:
        run(["ffmpeg", "-v", "error", "-y", "-hwaccel", "videotoolbox", "-i", A.src,
             "-c:v", "h264_videotoolbox", "-b:v", "12M", "-maxrate", "16M", "-bufsize", "24M", "-profile:v", "high", *common])
    except RuntimeError:  # kein Grafikchip-Encoder verfügbar → Software (langsamer, gleiches Ergebnis)
        run(["ffmpeg", "-v", "error", "-y", "-i", A.src, "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
             "-maxrate", "16M", "-bufsize", "24M", *common])
    before, after = os.path.getsize(A.src), os.path.getsize(out)
    log(f"Arbeitskopie: {before / 1e6:.0f} MB ({mbit:.0f} Mbit/s, {W}x{H}) → {after / 1e6:.0f} MB")
    STATS.update(work_copy_mb=[round(before / 1e6), round(after / 1e6)])
    return out


_wc = work_copy()
if _wc:
    A.src = _wc
    W, H, FPS, DUR, HAS_AUDIO = probe(A.src)
stage("Hört sich dein Video an (Sprache erkennen) …", 12)

# ─────────────────────────────── 2. Transkript ───────────────────────────────
wav = os.path.join(A.out, "audio.wav")
run(["ffmpeg", "-v", "error", "-y", "-i", A.src, "-vn", "-ac", "1", "-ar", "16000", wav])
# Spracherkennung auf dem Original-Ton: Tests (29.09.) zeigten, dass eine künstlich angehobene Spur mehr Wörter verhört
# („Mansell“ statt „Mandeln“). Leise Stellen fängt stattdessen die Hörprobe am fertigen, ausgeglichenen Ton auf (align).
wav_asr = wav

import mlx_whisper  # noqa: E402  (erst hier laden – dauert)


def detect_lang():
    """Sprache EINMAL für das ganze Video festlegen – aus drei Stellen (Anfang, Mitte, Ende), nicht nur den ersten 30 s
    (die sind oft Musik oder Rauschen). Danach hört jede Nachprüfung in genau dieser Sprache, sonst tauchen in kurzen
    Schnipseln englische Wörter auf („Thank you.“, „Cheering“). Deutsch gewinnt knappe Fälle (unser Markt)."""
    try:
        import mlx.core as mx
        from mlx_whisper.audio import load_audio, log_mel_spectrogram, pad_or_trim, N_FRAMES, N_SAMPLES
        from mlx_whisper.transcribe import ModelHolder
        model = ModelHolder.get_model(WHISPER, mx.float16)
        mel = log_mel_spectrogram(load_audio(wav_asr), n_mels=model.dims.n_mels, padding=N_SAMPLES)
        total = max(1, mel.shape[-2] - N_FRAMES)
        votes = {}
        for f in (0.15, 0.5, 0.82):
            f0 = int(max(0, min(total - N_FRAMES, total * f - N_FRAMES / 2)))
            _, probs = model.detect_language(pad_or_trim(mel[f0:f0 + N_FRAMES], N_FRAMES, axis=-2).astype(mx.float16))
            for k, v in probs.items():
                votes[k] = votes.get(k, 0.0) + v / 3
        best = max(votes, key=votes.get)
        STATS["lang_votes"] = {k: round(v, 2) for k, v in sorted(votes.items(), key=lambda x: -x[1])[:3]}
        return "de" if votes.get("de", 0) >= 0.3 else best
    except Exception as ex:  # noqa: BLE001
        log(f"Spracherkennung vorab nicht möglich ({ex}) – Deutsch")
        return "de"


LANG = detect_lang()
# alle Nachprüfungen: gleiche Sprache, lauter Ton
ASR = dict(path_or_hf_repo=WHISPER, word_timestamps=True, condition_on_previous_text=False, language=LANG)
tr = mlx_whisper.transcribe(wav_asr, **ASR, hallucination_silence_threshold=2.0,
                            initial_prompt=((BRAND + ". ") if BRAND else "") + ("Ähm, also, äh, ich wollte … ähm, ja. Genau." if LANG == "de" else ""))
WORDS = []
for seg in tr["segments"]:
    for w in seg.get("words", []):
        t = w["word"].strip()
        if t:
            WORDS.append({"t": t, "s": float(w["start"]), "e": float(w["end"])})
if len(WORDS) < 3:
    sys.exit("Im Video wurde keine Sprache erkannt.")
# Datum/Zahlen, die die Spracherkennung zerlegt („11.“ „05.“ → „11.05.“, „19“ „:30“ → „19:30“), wieder zusammenfügen
_m = []
for w in WORDS:
    if _m and re.fullmatch(r"\d{1,2}\.?", _m[-1]["t"]) and (re.fullmatch(r"[.:]\d{2}\.?[,!?]?", w["t"]) or (
            _m[-1]["t"].endswith(".") and re.fullmatch(r"\d{1,4}\.?[,!?]?", w["t"]))) \
            and w["s"] - _m[-1]["e"] < 0.35:
        _m[-1] = {**_m[-1], "t": _m[-1]["t"] + w["t"].lstrip("."), "e": w["e"]}
    else:
        _m.append(w)
WORDS = _m
for i, w in enumerate(WORDS):
    w["i"] = i
log(f"{len(WORDS)} Wörter, Sprache {LANG} {STATS.get('lang_votes', '')}")
stage("Versteht den Inhalt und sucht Anfang und Ende …", 35)

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


def syllables(t):
    return max(1, len(re.findall(r"[aeiouyäöü]+", t.lower())))


def expected_len(t):
    return 0.16 * syllables(t) + 0.12


def speech_frac(a, b):
    """Anteil 50-ms-Schritte zwischen a und b, in denen gesprochen wird (über der Stille-Schwelle)."""
    i0, i1 = max(0, int(a / 0.05)), min(len(LV), int(b / 0.05))
    return sum(1 for v in LV[i0:i1] if v >= NOISE_DB) / max(1, i1 - i0)


def recover_swallowed():
    """Whisper „verschluckt“ wiederholte Anläufe: aus „Wir sind 21 Jahre alt … wir sind 21 Jahre alt und …“ wird EIN Satz,
    der zweite Anlauf steckt unsichtbar in einem 3 s langen „und“ oder in einer Lücke. Solche Stellen einzeln neu abhören
    und die fehlenden Wörter einsetzen – sonst kann der Cutter den Fehlversuch gar nicht erkennen."""
    spots = []
    for k, w in enumerate(WORDS):
        if w["e"] - w["s"] > expected_len(w["t"]) + 0.8 and speech_frac(w["s"], w["e"]) > 0.5:
            spots.append((k, k, w["s"], w["e"]))
        if k + 1 < len(WORDS):
            g0, g1 = w["e"], WORDS[k + 1]["s"]
            if g1 - g0 > 1.0 and speech_frac(g0, g1) > 0.5:
                spots.append((k + 1, k, g0, g1))  # Lücke: nichts ersetzen, nur einfügen
    if not spots:
        return 0
    snip, added = os.path.join(A.out, "recover.wav"), 0
    for k0, k1, a, b in reversed(spots):  # von hinten, damit die Positionen stimmen
        x0, x1 = max(0.0, a - 0.15), min(DUR, b + 0.15)
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{x0:.3f}", "-to", f"{x1:.3f}", "-i", wav_asr, snip])
        try:
            r = mlx_whisper.transcribe(snip, **ASR, temperature=0.0)
            ws = [x for sg in r["segments"] for x in sg.get("words", []) if x["word"].strip()]
        except Exception:  # noqa: BLE001
            continue
        if len(ws) < 2 or _looks_hallucinated(ws):
            continue
        new = [{"t": x["word"].strip(), "s": x0 + x["start"], "e": x0 + x["end"]} for x in ws if x0 + x["end"] > a - 0.05]
        if k1 >= k0:  # langes Wort ersetzen – nur wenn das Wort selbst wieder vorkommt (sonst eher Fehlhörer)
            if len(new) < 2 or norm(WORDS[k0]["t"]) not in {norm(x["t"]) for x in new}:
                continue
            WORDS[k0:k1 + 1] = new
            added += len(new) - 1
        else:
            if len(new) < 2:
                continue
            WORDS[k0:k0] = new
            added += len(new)
    for i, w in enumerate(WORDS):
        w["i"] = i
    return added


def _looks_hallucinated(ws):
    toks = [norm(w["word"]) for w in ws]
    zero = sum(1 for w in ws if w["end"] - w["start"] <= 0.001)
    if len(ws) > 30 or (len(ws) > 6 and zero > len(ws) * 0.3):
        return True
    return any(toks[i] and toks[i] == toks[i + 2] == toks[i + 4] for i in range(len(toks) - 4))



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
    # Ordner-Test des Inhabers 07.10. („Die Gewinner, äh, die Gewinner sind die drei Top-Kommentare“): Zwischen den beiden
    # Anläufen stand ein Füllwort – so wurde der Neustart nicht erkannt, blieb als Stotterer im Video, und die Schlusskontrolle
    # strich daraufhin den GANZEN Satz. Derselbe Vergleich deshalb noch einmal ohne Füllwörter: erster Anlauf + Füllwort raus.
    idx = [j for j in range(n) if toks[j] and toks[j] not in FILLER]
    for p in range(len(idx)):
        for L in (4, 3, 2):
            if p + 2 * L > len(idx):
                continue
            ia, ib = idx[p:p + L], idx[p + L:p + 2 * L]
            if ib[0] - ia[-1] < 2:      # ohne Füllwort dazwischen ist es der Fall oben
                continue
            a, b = [toks[j] for j in ia], [toks[j] for j in ib]
            same = a == b or (a[:-1] == b[:-1] and len(a[-1]) >= 2 and b[-1].startswith(a[-1][:2]))
            if same and not SENT_END.search(words[ia[-1]]["t"]):
                out.update(words[j]["i"] for j in range(ia[0], ib[0]))
                break
    return out


def gemini(system, user, schema, parts=None, timeout=45, models=None, deep=False):
    """Gemini über ki.py (Gratis-Schlüssel zuerst, Modelle nach Wichtigkeit, Tageslimits umgehen). None ohne KI – dann gelten
    nur die Regeln. parts: z. B. ein Video. deep=True für Entscheidungen, bei denen Nachdenken die Qualität hebt."""
    res = ki.call(system, (parts or []) + [{"text": user}], schema, deep=deep, timeout=timeout, models=models)
    if res is not None:
        STATS["gemini_model"] = ki.TOK.get("modell")
    t = STATS.setdefault("ki_tokens", {"aufrufe": 0, "ein": 0, "aus": 0, "denken": 0})
    for k, v in list(ki.TOK.items()):  # Verbrauch übernehmen, Zähler zurücksetzen (sonst doppelt gezählt)
        if isinstance(v, int):
            t[k] = t.get(k, 0) + v
            ki.TOK[k] = 0
    return res


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
        "und endet erst, wenn der Gedanke abgeschlossen ist (nie mitten im Satz). " + STORY_RULES + " Gib Start- und End-Wortnummer an (inklusive) und einen "
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


CUTTER = (
    "Du bist ein erfahrener persönlicher Cutter für Kurzvideos (TikTok, Reels, Shorts). Kunden laden KOMPLETTES Rohmaterial hoch: "
    "Die Kamera läuft schon, bevor die Szene beginnt, und läuft danach weiter. Mittendrin versprechen sich Leute, brechen ab und "
    "sagen denselben Satz einfach nochmal, ohne neu anzufangen; sie reden mit dem Kameramann oder sagen Dinge, die nicht zum Video "
    "gehören. Der Zuschauer darf NIE merken, dass gedreht, abgesprochen oder wiederholt wurde – das fertige Video wirkt wie aus einem Guss. ")
STORY_RULES = (
    "Regeln: "
    "1) START erst mit der eigentlichen Szene – wenn die Person die andere anspricht bzw. wirklich zum Zuschauer spricht. Alles davor "
    "raus: Vorgespräch, Absprachen („nehmt ihr dann den?“, „bist du so weit?“, „läuft?“), Fehlstarts. Bei „nochmal von vorne“, "
    "„noch mal“, „cut“ o. Ä. gilt nur der LETZTE Versuch. "
    "2) ENDE, sobald der Inhalt abgeschlossen ist: nach der Auflösung/dem Gewinn plus höchstens EINEM abschließenden Satz. "
    "Bei Gesprächen/Umfragen mit einem Gegenüber gehört danach ein KURZER ABSCHIED als Schlusspunkt dazu (Nutzer 06.10.: „bis "
    "sie Tschüss sagen, ciao und danke – dann ist das Video vorbei“): ein einziger Wechsel wie „Dankeschön!“ – „Tschüss!“. "
    "Langes Pingpong („Danke fürs Mitmachen“ – „Gerne“ – „Tschüss“ – „Tschüss“ – „Schönen Abend noch“) auf diesen einen Wechsel "
    "kürzen; Nebensächliches zwischen Auflösung und Abschied (Bedienung erklären, Namen wiederholen, Smalltalk) weiter raus. "
    "Der Abschied kommt ZUSÄTZLICH zur Länge aus Regel 6 (2–3 Sekunden mehr sind in Ordnung) – dafür wird nie Inhalt gestrichen "
    "(Test 06.10.: für den Abschied flog die Frage „Folgst du uns schon auf Instagram?“ samt Antwort raus, übrig blieb der "
    "unverständliche Rest „baglet.mannheim“). Wirklich nur EIN Wechsel: von „Dankeschön – Vielen lieben Dank und schönen Tag "
    "noch – Danke schön – Tschüss“ bleiben z. B. „Vielen lieben Dank und schönen Tag noch!“ – „Tschüss!“. "
    "Monolog/Promo ohne Gegenüber: endet hart auf dem Aufruf („kommt vorbei“). Alles NACH dem Abschied sowieso raus (Umdrehen zum Kameramann, „okay super, passt?“, Nachgespräch). "
    "3) MITTENDRIN raus: abgebrochene oder verpatzte Anläufe, wenn der Satz danach nochmal (besser) gesagt wird – nur die letzte "
    "vollständige Fassung bleibt; Sätze an Kameramann/Team; Bemerkungen, die nicht zum Thema gehören oder ohne Vorwissen keinen Sinn ergeben. "
    "4) Test für jeden Satz: Würde ein Zuschauer ihn vermissen, oder wird ohne ihn etwas unverständlich? Wenn nein und er stört – raus. "
    "Antworten und Reaktionen der anderen Person, Lacher und Pointen gehören zur Szene und bleiben. Inhaltlich Wichtiges bleibt immer. "
    "5) Nie mitten im Satz beginnen oder enden. "
    "6) LÄNGE: so kurz wie möglich bei vollem Inhalt. Straßen-Umfrage mit EINER Person/Runde (Herkunft/Hook → Probieren → "
    "Bewertung → Preis raten + Auflösung): 15–25 Sekunden, das sind meist nur 25–35 % des Rohmaterials (so schneidet der Nutzer "
    "– 18 von 18 Umfragen). Quiz mit mehreren Fragen, Vorstellung, Erklärung: 30–60 Sekunden, höchstens 90. Bei langem Rohmaterial die stärkste "
    "zusammenhängende Geschichte behalten: Aufhänger (Frage/Aktion) → Hauptteil → AUFLÖSUNG (Ergebnis, Gewinner, Bewertung, "
    "Reaktion). Nie vor der Auflösung aufhören (z. B. nicht bei „3, 2, 1, los!“ enden). Wiederholte oder schwächere Passagen "
    "(z. B. dieselbe Frage an die dritte Person mit gleicher Antwort) dürfen raus. "
    "7) TEMPO wie ein Profi-Cutter (gelernt aus den Schnitten des Nutzers, 117 s Rohmaterial → 51 s): Begrüßung höchstens ein "
    "kurzer Satz, dann SOFORT der Aufhänger (Spielregeln, Frage, Versprechen) – Smalltalk und Erklärungen davor raus. "
    "Rate- und Verhandlungsschleifen („70er? – höher – 80er? – bisschen mehr – 86? – höher – 87?“) auf die letzten 1–2 Tipps "
    "plus Auflösung kürzen. Einzelne Bestätigungen/Füllsätze zwischen den Abschnitten („Okay.“, „Ja.“, „Dann,“, „Passt.“, "
    "„Mach auf.“, „Okay.“ nach einer Antwort) raus, wenn sie nichts beitragen. Erklärungen von Spielregeln/Skalen („1 ist "
    "schlecht, 10 ist gut“), Rückfragen („Was?“, „1 oder 10?“) und Mikrofon-/Technik-Sätze raus. Hilfen/Tipps des Moderators nur behalten, wenn die Antwort sonst unverständlich ist. "
    "Die Kern-Schritte des Formats (bei Umfragen: Bewertung UND Preis raten + Auflösung) bleiben, wenn sie verständlich sind – "
    "auch eine falsche Schätzung mit kurzer Auflösung („5 Euro? – Nein, tatsächlich 3,99“) ist Inhalt. Faustregel: Frage + "
    "Antwort + Auflösung in höchstens ca. 3 Wortwechseln bzw. 8 Sekunden → bleibt. Ufert der Teil aus (mehr Wortwechsel, "
    "Rückfragen, Missverständnis, Korrekturen, Abschweifen zu anderen Produkten), diesen Teil GANZ weglassen – eine kurze, "
    "klare Geschichte ist besser als eine vollständige, verworrene. Ortsangabe/Aufruf am Ende („Weißt du, wo wir sind? – C1 8“, "
    "„Kommt vorbei“) gehört zum Abschluss und bleibt. "
    "8) KEINE HALBEN SACHEN (Nutzer 06.10.: „er fängt einen Satz an und dann wird er einfach weggeschnitten“): Ein Satz, der "
    "angefangen, aber nicht zu Ende gesprochen wird, fliegt GANZ raus – nie den Anfang stehen lassen. Ebenso Halbsätze, die "
    "etwas ankündigen, das danach nicht kommt oder im Bild nicht zu sehen ist („und wenn das Brot fertig ist …“, danach geht es "
    "um Käse): ganz weglassen. Jeder Satz, der bleibt, muss für sich vollständig sein und zum nächsten passen. "
    "9) PANNEN GEHÖREN NIE INS VIDEO (Inhaber 06.10.: „‚das Fleisch ist verbrannt‘ darf man nicht hören – egal ob Clean, Pro oder "
    "Premium“): alles, was der Sprecher über ein Missgeschick, einen Fehler oder seine eigene Unsicherheit sagt („das ist "
    "vielleicht verbrannt“, „warte“, „scheiße“, „wie heißt das?“, „hab ich vergessen“, „nee, anders“), fliegt KOMPLETT raus – "
    "auch als Halbsatz oder Nebensatz. Enthält ein sonst nützlicher Satz so einen Teil („wir holen es runter, dass es nicht "
    "verbrannt ist“), den ganzen Satz weglassen, wenn sich der Teil nicht sauber abtrennen lässt. Ein Werbevideo zeigt, wie es "
    "gelingt, nicht was schiefging.")
STORY_EXAMPLES = (
    "Beispiele (| trennt Sätze): "
    "A) „Nehmt ihr dann den? | Ja. | Okay. | Hallo. | Hallo, ich hab gehört … | Nochmal von vorne. | Hallo! Wollt ihr einen "
    "Kaiserschmarrn probieren? | Ja, bitte. …“ → Start bei „Hallo! Wollt ihr einen Kaiserschmarrn probieren?“. "
    "B) „… Dankeschön, es hat uns gefreut. Tschüss! | Tschüss! | Okay super, passt? | Ja, passt.“ → Ende nach dem letzten „Tschüss!“. "
    "C) „Heute zeige ich euch, wie man in fünf. | Warte. | Heute zeige ich euch, wie man in fünf Minuten Pizza macht.“ → die ersten "
    "zwei Sätze raus. "
    "D) „Das kostet nur zehn Euro, äh, nee, zwölf. | Das kostet nur zwölf Euro.“ → nur der zweite Satz bleibt. "
    "E) Quiz: „In welchem Jahr ist die Mauer gefallen? | 19-hundert irgendwas? | Ich habe keine Ahnung. | Vielleicht so im 70er, "
    "60er Bereich? | Bisschen mehr. | Also höher? | Höher, ja. | 80er Bereich? | Ja. | Vielleicht 86. | Bisschen höher. | 88. | 89. "
    "| Richtig!“ → bleibt: Frage | „19-hundert irgendwas?“ | „88.“ | „89.“ | „Richtig!“. "
    "F) Mehrfach-Take: „Hallo. | Wir sind ja hier am Paradeplatz. | Was? | Wir sind ja gerade hier am Paradeplatz. | Kommst du aus "
    "Mannheim? | Ja.“ → bleibt: „Wir sind ja gerade hier am Paradeplatz. | Kommst du aus Mannheim? | Ja.“ "
    "G) Ende: „Tatsächlich kostet er 1,99 Euro. | Wir freuen uns, wenn du vorbeikommst. | Okay, komm ich vorbei. | Tschüss! | "
    "Ciao!“ → bleibt bis „Tschüss! | Ciao!“ (kurzer Abschied = Schlusspunkt), danach nichts mehr. "
    "H) Regel-Erklärung: „Bewerte ihn von 1 bis 10. | Das ist gut, 1 oder 10? | 1 ist nicht so gut und 10 ist sehr gut. | Ganz "
    "gut, gell? | 8.“ → bleibt: „Bewerte ihn von 1 bis 10.“ | „8.“ ")


def gemini_story(words):
    """Rohmaterial: KI entscheidet Satz für Satz wie ein Cutter, was ins fertige Video gehört (Anfang, Ende, Mittendrin)."""
    sents = sentences(words)
    ask = lambda: gemini(
        CUTTER + "Du bekommst das Transkript Satz für Satz als [Nummer|Sekunde] Text. " + STORY_RULES + " " + STORY_EXAMPLES +
        "Bestimme zuerst das Format (z. B. „Straßen-Umfrage, eine Person“, „Quiz mit mehreren Fragen“, „Vorstellung“, "
        "„Monolog“) und die ideale Länge des fertigen Videos in Sekunden (ziel_s). Antworte dann mit den Nummern ALLER Sätze, "
        "die ins fertige Video gehören (in Reihenfolge), und für jeden entfernten Satz einen kurzen Grund.",
        "\n".join(f"[{k}|{s[0]['s']:.1f}s] {' '.join(w['t'] for w in s)}" for k, s in enumerate(sents)),
        {"type": "object", "properties": {
            "format": {"type": "string"}, "ziel_s": {"type": "integer"},
            "keep": {"type": "array", "items": {"type": "integer"}},
            "removed": {"type": "array", "items": {"type": "object", "properties": {
                "n": {"type": "integer"}, "why": {"type": "string"}}, "required": ["n", "why"]}}},
         "required": ["format", "ziel_s", "keep"]}, deep=True)
    # Drei unabhängige Entscheidungen, ein Satz bleibt bei Mehrheit (Prüfstand 01.10.: einzelne Durchläufe schwankten
    # bei derselben Umfrage zwischen 17 und 31 s) – parallel, kostet kaum Zeit
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(3) as ex:
        runs = [r for r in ex.map(lambda _: ask(), range(3)) if r and r.get("keep")]
    if not runs:
        return None
    votes = {}
    for r in runs:
        for k in {k for k in r["keep"] if isinstance(k, int) and 0 <= k < len(sents)}:
            votes[k] = votes.get(k, 0) + 1
    keep = sorted(k for k, v in votes.items() if 2 * v > len(runs))
    if not keep:
        return None
    res = max(runs, key=lambda r: len(set(r["keep"]) & set(keep)))  # Begründungen aus dem Durchlauf, der der Mehrheit am nächsten ist
    ziele = sorted(int(r.get("ziel_s") or 0) for r in runs if 8 <= int(r.get("ziel_s") or 0) <= 90)
    ziel = ziele[len(ziele) // 2] if ziele else 45
    span = lambda ks: sum(sents[k][-1]["e"] - sents[k][0]["s"] for k in ks)
    kept_words = sum(len(sents[k]) for k in keep)
    if span(keep) < 0.25 * span(range(len(sents))) and (span(keep) < 2.0 or kept_words < 4):  # Sicherung: KI wirft fast alles weg
        log(f"Story (KI) verworfen: nur {span(keep):.1f} s von {span(range(len(sents))):.1f} s behalten")
        return None
    def est_len(ks):
        ws = [w for k in ks for w in sents[k]]
        tot = sum(w["e"] - w["s"] for w in ws)
        for a, b in zip(ws, ws[1:]):
            g = b["s"] - a["e"]
            if g > 0:
                tot += min(g, 2.0) if speech_frac(a["e"], b["s"]) > 0.3 else min(g, 0.2)  # Pausen bleiben nur als kurze Luft
        return tot
    STATS["story_format"] = {"format": (res.get("format") or "")[:60], "ziel_s": ziel, "laeufe": len(runs)}
    goal = min(70, ziel)
    if est_len(keep) / 1.1 > max(goal * 1.3, goal + 6):  # deutlich länger als das Format verträgt → gezielt straffen
        res2 = gemini(
            CUTTER + f"Die ausgewählte Szene ist ca. {est_len(keep):.0f} Sekunden lang – zu lang für dieses Format ({res.get('format') or 'Kurzvideo'}). "
            f"Kürze sie auf ca. {goal} Sekunden: Behalte den Aufhänger am Anfang und die Auflösung am Ende (Ergebnis, Gewinner, Bewertung, "
            "Preis, Ortsangabe/„kommt vorbei“) und die stärksten Momente dazwischen; streiche Wiederholungen, Nebensächliches, lange Erklärungen, "
            "schwächere Antworten. Die Geschichte muss ohne die gestrichenen Sätze verständlich bleiben. Satz-Nummern der Sätze, "
            "die bleiben, in Reihenfolge. Sätze als [Nummer|Dauer] Text.",
            "\n".join(f"[{k}|{sents[k][-1]['e'] - sents[k][0]['s']:.1f}s] {' '.join(w['t'] for w in sents[k])}" for k in keep),
            {"type": "object", "properties": {"keep": {"type": "array", "items": {"type": "integer"}}}, "required": ["keep"]}, deep=True)
        k2 = sorted({k for k in (res2 or {}).get("keep") or [] if k in keep})
        if k2 and min(12, goal * 0.6) <= est_len(k2) <= 90:
            log(f"Story (KI) gestrafft: ca. {est_len(keep):.0f} s → {est_len(k2):.0f} s")
            keep = k2
    keep = drop_doubles(sents, keep)
    drop = {w["i"] for k in range(keep[0], keep[-1] + 1) if k not in keep for w in sents[k]}
    why = [f"{' '.join(w['t'] for w in sents[r['n']])[:50]} → {r['why'][:60]}"
           for r in res.get("removed") or [] if isinstance(r.get("n"), int) and 0 <= r["n"] < len(sents)]
    STATS["story"] = {"start": sents[keep[0]][0]["t"] + " …", "kept": len(keep), "of": len(sents), "removed": why[:20]}
    log(f"Story (KI): Satz {keep[0]}–{keep[-1]} von {len(sents)}, {len(drop)} Wörter mittendrin raus")
    for x in why[:12]:
        log("  raus:", x)
    return {"start": sents[keep[0]][0]["i"], "end": sents[keep[-1]][-1]["i"], "title": "", "drop": drop}


def drop_doubles(sents, keep):
    """Doppelt-Check (Prüfstand 03.10.: unsere Umfragen 14 % länger als die des Nutzers – meist wegen Doppeltem): dieselbe
    Frage zweimal gestellt (auch anders formuliert), korrigierte Antworten („8. – 9.“), dieselbe Info nochmal. Nur die
    LETZTE/beste Fassung bleibt. Zwei unabhängige KI-Durchläufe – raus fliegt nur, worin beide übereinstimmen."""
    if len(keep) < 4:
        return keep
    ask = lambda: gemini(
        CUTTER + "Diese Sätze sind schon für das fertige Video ausgewählt. Finde nur noch DOPPELTES: dieselbe Frage zweimal "
        "gestellt (auch anders formuliert, z. B. „Was denkst du, was der kostet?“ … „Wie viel kostet der wohl?“), eine Antwort, "
        "die gleich danach korrigiert oder wiederholt wird („8. – 9.“, „2 Euro? – Ja, 2 Euro.“), dieselbe Info zweimal. "
        "Gib die Nummern der FRÜHEREN/schwächeren Fassungen zurück, die raus sollen – die letzte, vollständige Fassung bleibt. "
        "Nichts anderes streichen; wenn nichts doppelt ist, leere Liste.",
        "\n".join(f"[{k}] {' '.join(w['t'] for w in sents[k])}" for k in keep),
        {"type": "object", "properties": {"raus": {"type": "array", "items": {"type": "integer"}}}, "required": ["raus"]}, deep=True)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(2) as ex:
        runs = [set((r or {}).get("raus") or []) for r in ex.map(lambda _: ask(), range(2))]
    if len(runs) < 2:
        return keep
    raus = (runs[0] & runs[1]) & set(keep[:-1])  # nie den letzten Satz (Auflösung/Aufruf)
    if not raus or len(raus) > 0.3 * len(keep):
        return keep
    STATS["doppelt"] = [" ".join(w["t"] for w in sents[k])[:50] for k in sorted(raus)]
    log("Doppelt raus:", " | ".join(STATS["doppelt"]))
    return [k for k in keep if k not in raus]


# Sätze an Kameramann/Team: eindeutige Formulierungen (Satz bis 6 Wörter) bzw. kurze Einwürfe (bis 3 Wörter)
CREW = re.compile(r"(\bpasst( das| es| so)?\s*\?|^passt[.!]?$|\bläuft('?s| es| die kamera)?\s*\?|nimmst du (schon )?auf|"
                  r"(bist du|seid ihr) (so ?weit|bereit)|hast du('?s| es) (drauf|drin)|ist (das|es) drin|kamera läuft|"
                  r"noch ?mal (bitte|von vorne)|von vorne|\baction\b|\bcut\b)", re.I)
CREW_SHORT = re.compile(r"^(sorry|warte|wartet|moment|stopp?|nee warte|sekunde)\b", re.I)


def retake_idx(words):
    """Ohne KI (immer aktiv): Anläufe, die gleich danach nochmal gesagt werden – auch ohne „von vorne“ –
    und kurze Sätze an den Kameramann → raus. Nur die letzte vollständige Fassung bleibt."""
    sents = sentences(words)
    toks = [[norm(w["t"]) for w in s if norm(w["t"]) and norm(w["t"]) not in FILLER] for s in sents]
    text = [" ".join(w["t"] for w in s).strip() for s in sents]
    crew = {k for k in range(len(sents)) if (len(toks[k]) <= 6 and CREW.search(text[k])) or
            (len(toks[k]) <= 3 and CREW_SHORT.search(text[k]))}
    out = {w["i"] for k in crew for w in sents[k]}
    keep = set()  # die letzte, vollständige Fassung – darf von niemandem (auch nicht von der KI) entfernt werden
    for a in range(len(sents)):
        ta = toks[a]
        if len(ta) < 3 or a in crew:
            continue
        for b in range(a + 1, min(len(sents), a + 4)):
            tb = toks[b]
            if sents[b][0]["s"] - sents[a][-1]["e"] > 15:
                break
            if len(tb) >= 3 and b not in crew:
                sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
                covered = sum(m.size for m in sm.get_matching_blocks())
                abandoned = len(ta) < len(tb) and covered >= 0.8 * len(ta) and ta[:2] == tb[:2]  # „wie man in fünf.“ → „wie man in fünf Minuten …“
                same = sm.ratio() >= 0.85  # fast wortgleich nochmal gesagt
                if abandoned or same:
                    out.update(w["i"] for k in range(a, b) for w in sents[k])
                    keep.update(w["i"] for w in sents[b])
                    break
            if b not in crew and len(tb) > 3:
                break  # dazwischen echter Inhalt → keine Wiederholung (z. B. gleiche Frage an eine andere Person)
    # Neustart innerhalb eines Satzes: „ich war gestern in der ich war gestern in der Stadt“ → erster Anlauf raus
    tk, j = [norm(w["t"]) for w in words], 0
    while j < len(words):
        k = next((k for k in range(j + 1, min(len(words) - 3, j + 13)) if tk[j] and tk[j:j + 3] == tk[k:k + 3]
                  and (k - j <= 7 or tk[j + 3] == tk[k + 3])
                  and not any(SENT_END.search(w["t"]) for w in words[j:k])), None)
        if k is None:
            j += 1
            continue
        out.update(w["i"] for w in words[j:k])
        keep.update(w["i"] for w in words[k:k + (k - j)])
        j = k  # ab dem neuen Anlauf weitersuchen (nicht den neuen Anlauf selbst wegschneiden)
    # Abbruch mit Signalwort: „ihr wolltet – warte – ihr wolltet Strawberry …“ (nur 2 gleiche Wörter, aber das „warte“
    # verrät den Neustart; Cheesecake-Ordner 03.10.) → erster Anlauf samt Signalwort raus
    for m in range(1, len(words) - 2):
        if tk[m] not in RESTART_WORDS:
            continue
        j = next((j for j in range(m - 1, max(-1, m - 9), -1) if tk[j] and tk[j:j + 2] == tk[m + 1:m + 3]), None)
        if j is not None and not any(SENT_END.search(w["t"]) for w in words[j:m - 1]):
            gone = {w["i"] for w in words[j:m + 1]}
            keep.difference_update(gone)  # das Signalwort schlägt den Schutz „letzte Fassung“ eines früheren Anlaufs
            out.update(gone)
            keep.update(w["i"] for w in words[m + 1:m + 3])
    return out - keep, keep


RESTART_WORDS = {"warte", "wartet", "sorry", "moment", "stopp", "stop", "nochmal", "nee"}


RETAKE = re.compile(r"(noch ?mal von vorne|von vorne|noch ?mal neu|neuer versuch|\bcut\b|ich fang (noch ?mal )?neu an)", re.I)
FAREWELL = {"tschüss", "tschau", "ciao", "bye", "servus", "danke", "dankeschön", "tschüssi", "wiedersehen"}


def rule_story(words):
    """Ohne KI: nach einem Neustart („nochmal von vorne“) erst ab dem letzten Versuch; nach dem Abschied Schluss."""
    start, end = 0, len(words) - 1
    text_pos, joined = [], ""
    for w in words:
        text_pos.append(len(joined))
        joined += w["t"] + " "
    for m in RETAKE.finditer(joined):
        k = max(i for i, p in enumerate(text_pos) if p <= m.start())
        if words[k]["s"] < DUR * 0.6:
            nxt = next((i for i in range(k, len(words)) if SENT_END.search(words[i]["t"])), k)
            start = min(len(words) - 1, nxt + 1)
    last_bye = max((i for i, w in enumerate(words) if norm(w["t"]) in FAREWELL and i > start), default=None)
    if last_bye is not None and words[-1]["e"] - words[last_bye]["e"] < 8:
        end = last_bye
    return {"start": start, "end": end, "title": "", "drop": set()}


_n = recover_swallowed()
if _n:
    log(f"Nachgehört: {_n} verschluckte Wörter wiederhergestellt")
    STATS["recovered"] = _n

def gemini_proofread(words):
    """KI-Lektorat: Die Spracherkennung verhört sich bei Straßenlärm („Schmarm“, „kommst du aus Mandel?“) und schreibt manchmal
    alles klein ohne Satzzeichen. Die KI schreibt den Text als sauberen Fließtext (darin sind Sprachmodelle am zuverlässigsten),
    die Software ordnet ihn Wort für Wort wieder zu (gleiche Wörter → Schreibweise/Satzzeichen übernehmen, 1:1 ersetzte Wörter
    → Korrektur übernehmen). Unverständlicher Kauderwelsch bekommt KEINEN Untertitel (lieber keiner als ein falscher)."""
    import difflib
    fixed = unclear = 0
    BRAND_N = {norm(x) for x in re.split(r"[,;\s]+", BRAND or "") if norm(x)}
    for c0 in range(0, len(words), 150):
        chunk = words[c0:c0 + 150]
        res = gemini(
            "Du bist Lektor:in für Untertitel deutscher Kurzvideos (Straßeninterviews, Restaurants, Food, Monologe). Unten steht ein "
            "automatisches Transkript (evtl. kleingeschrieben, ohne Satzzeichen, teils verhört). Schreibe es als sauberen "
            "Untertiteltext: korrekte Rechtschreibung, Groß-/Kleinschreibung, Satzzeichen; offensichtlich verhörte Wörter und "
            "verstümmelte Namen aus dem Zusammenhang korrigieren (z. B. „Schmarm“ → „Schmarrn“ an einem Kaiserschmarrn-Stand). "
            "Umgangssprache/Dialekt bleibt. Wörter NICHT umstellen, nichts weglassen, nichts hinzufügen, NICHTS umformulieren oder durch ein anderes Wort ersetzen – es muss genau das dastehen, was gesagt wurde. Zusätzlich: Liste der "
            "Wortfolgen aus dem ORIGINAL, die sinnloser Kauderwelsch sind und sich nicht sicher korrigieren lassen." +
            (f" Diese Namen/Marken kommen im Video vor – verhörte Varianten genau so schreiben: {BRAND}." if BRAND else ""),
            " ".join(w["t"] for w in chunk),
            {"type": "object", "properties": {
                "text": {"type": "string"},
                "unclear": {"type": "array", "items": {"type": "string"}}}, "required": ["text"]})
        if not res or not (res.get("text") or "").strip():
            continue
        new = res["text"].split()
        sm = difflib.SequenceMatcher(a=[norm(w["t"]) for w in chunk], b=[norm(t) for t in new], autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
                for d in range(i2 - i1):
                    w, t = chunk[i1 + d], new[j1 + d]
                    if t != w["t"]:
                        # Regel 91: Der Untertitel zeigt, was GESAGT wurde. Ein anderes Wort nur, wenn es fast gleich klingt
                        # (verhört: „Bürger“ → „Burger“) oder ein Kunden-Name ist – nie „schöner formulieren“.
                        na, nb = norm(w["t"]), norm(t)
                        if na != nb and nb not in BRAND_N and difflib.SequenceMatcher(a=na, b=nb).ratio() < 0.4:
                            continue
                        w["t"] = t
                        fixed += 1
            elif tag == "replace" and i2 - i1 == 2 and j2 - j1 == 1:  # zusammengeschrieben („weiter empfehlen“ → „weiterempfehlen“)
                t = new[j1]
                a0, a1 = chunk[i1]["t"], chunk[i1 + 1]["t"]
                if norm(a0 + a1) == norm(t):  # Schreibweise vom Original, Satzzeichen/Großschreibung von der Korrektur
                    head = a0.lower() if t[:1].islower() else a0[:1].upper() + a0[1:].lower()
                    tail = re.search(r"[.,!?;:…]+$", t)
                    chunk[i1]["t"], chunk[i1 + 1]["t"] = head, a1.lower() + (tail.group(0) if tail else "")
                    fixed += 2
        toks = [norm(w["t"]) for w in chunk]
        for ph in res.get("unclear") or []:
            pn = [norm(x) for x in str(ph).split() if norm(x)]
            for k in range(len(toks) - len(pn) + 1):
                if pn and toks[k:k + len(pn)] == pn:
                    for w in chunk[k:k + len(pn)]:
                        if not w.get("unclear"):
                            w["unclear"] = True
                            unclear += 1
    if fixed or unclear:
        log(f"Lektorat (KI): {fixed} Wörter korrigiert, {unclear} unverständliche ohne Untertitel")
    STATS.update(proofread_fixed=fixed, proofread_unclear=unclear)


gemini_proofread(WORDS)

SHORT = DUR <= 300  # Rohmaterial bis 5 Min. = eine Szene → ein Clip, der die ganze Geschichte erzählt (inkl. Auflösung)
if PLAN:  # mehrere Clips: die Auswahl der Sätze hat montage.py schon getroffen → alles nehmen, nur noch sauber schneiden
    MOMENTS = [{"start": 0, "end": len(WORDS) - 1, "title": PLAN.get("titel") or "", "drop": set()}]
elif SHORT:
    MOMENTS = [gemini_story(WORDS) or rule_story(WORDS)]
else:
    # Inhaber 06.10.: derselbe Link noch einmal → der NÄCHSTBESTE Clip (nicht wieder derselbe). Es werden entsprechend mehr
    # Momente gesucht und die schon gelieferten übersprungen; gibt das Video nicht so viele her, die letzten gefundenen.
    _n = A.clips + max(0, A.skip)
    _all = [m for m in (gemini_moments(WORDS, _n) or []) if 0 <= m["start"] <= m["end"] < len(WORDS)][:_n] or rule_moments(WORDS, _n)
    MOMENTS = _all[A.skip:A.skip + A.clips] or _all[-A.clips:]
    if A.skip:
        log(f"Link wurde schon {A.skip}× geschnitten → Moment {min(A.skip, max(0, len(_all) - A.clips)) + 1} von {len(_all)} gefundenen")
ki.require_or_exit("Satzauswahl")
stage("Schneidet Pausen, Füllwörter und Versprecher raus …", 45)


def word_gaps():
    """Pausen zwischen zwei Wörtern, die zwar nicht still sind (Straße, Wind, Musik), aber deutlich leiser als die Stimmen
    davor/danach (≥ 6 dB) – der Nutzer schneidet auch dort hart zusammen (Median 0,08 s Luft an Schnitten)."""
    lv = lambda a, b: LV[max(0, int(a / 0.05)):max(int(a / 0.05) + 1, int(b / 0.05))]
    out = []
    for a, b in zip(WORDS, WORDS[1:]):
        g0, g1 = a["e"] + 0.03, b["s"] - 0.03
        if g1 - g0 < 0.3 or not LV:
            continue
        gap, voice = lv(g0, g1), lv(a["s"], a["e"]) + lv(b["s"], b["e"])
        if gap and voice and sorted(gap)[len(gap) // 2] <= sorted(voice)[len(voice) // 2] - 6.0:
            out.append((g0, g1))
    return out


GAPS = word_gaps()
log(f"Leise Stellen zwischen Wörtern: {len(GAPS)} ({sum(b - a for a, b in GAPS):.1f} s)")

# Luft, die an jeder geschnittenen Pause stehen bleibt (je Seite) – Clean natürlich, Viral knackiger
PAUSE_SIDE = 0.06 if LOUD else 0.1


def _clip_wav(a, b, path):
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, a):.3f}", "-to", f"{b:.3f}", "-i", wav_asr, path])


def cut_score(w, a, b):
    """Stelle (mit Kontext) ohne [a, b] neu abhören. 0 = Wort nicht mehr erkennbar, sonst Erkennungs-Sicherheit (0..1)."""
    x0, x1 = max(0.0, w["s"] - 0.8), min(DUR, w["e"] + 0.8)
    tmp = os.path.join(A.out, "check.wav")
    run(["ffmpeg", "-v", "error", "-y", "-i", wav_asr, "-af",
         f"aselect='between(t,{x0:.3f},{a:.3f})+between(t,{b:.3f},{x1:.3f})',asetpts=N/SR/TB", tmp])
    try:
        r = mlx_whisper.transcribe(tmp, **ASR, temperature=0.0)
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
            r = mlx_whisper.transcribe(snip, **ASR, temperature=0.0, initial_prompt="Äh, ähm, also." if LANG == "de" else None)
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


_PANS = []


def pans():
    """Regel 96 (Nutzer 05.10.: „die Kamera schwenkt rüber zu den Beilagen – das will keiner sehen, da schaltet man ab“):
    Strecken finden, in denen die Kamera nur von A nach B schwenkt (≥ 0,8 s lang mehr als ~½ Bildbreite pro Sekunde).
    Einmal für das ganze Material messen (10 Bilder/s, klein)."""
    if _PANS:
        return _PANS[0]
    import numpy as np
    import cv2
    R, SW, SH = 10, 160, 284
    out = []
    try:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", A.src, "-vf", f"fps={R},scale={SW}:{SH},format=gray", "-f", "rawvideo", "-"],
                             capture_output=True, stdin=subprocess.DEVNULL, timeout=900).stdout
        n = len(raw) // (SW * SH)
        fr = np.frombuffer(raw[: n * SW * SH], np.uint8).reshape(n, SH, SW).astype(np.float32)
        win = cv2.createHanningWindow((SW, SH), cv2.CV_32F)
        v = np.array([np.hypot(*cv2.phaseCorrelate(fr[i], fr[i + 1], win)[0]) for i in range(n - 1)])
        v = np.array([np.median(v[max(0, i - 2): i + 3]) for i in range(len(v))])  # einzelne Ausreißer (harte Schnitte) zählen nicht
        i = 0
        while i < len(v):
            if v[i] >= 8:
                j, last = i, i
                while j < len(v) and (v[j] >= 5 or j - last <= 3):
                    if v[j] >= 8:
                        last = j
                    j += 1
                if (last + 1 - i) / R >= 0.8:
                    out.append((i / R, (last + 1) / R))
                i = max(j, i + 1)
            else:
                i += 1
    except Exception as e:  # noqa: BLE001
        log(f"Schwenk-Erkennung übersprungen: {str(e)[:80]}")
    if out:
        log("Kameraschwenks: " + ", ".join(f"{a:.1f}–{b:.1f} s" for a, b in out))
    STATS["schwenks"] = [(round(a, 1), round(b, 1)) for a, b in out]
    _PANS.append(out)
    return out


def keep_ranges(words, safe=False, extra_drop=frozenset()):
    """Zeitbereiche, die bleiben: ohne Füllwörter/Wiederholungen, Stille raus, Schnitte nur zwischen Wörtern."""
    fil, rep, gem = filler_idx(words), repeat_idx(words), gemini_cuts(words)
    ret, final = retake_idx(words)
    drop = (fil | rep | gem | ret | set(extra_drop)) - (final - fil)  # letzte Fassung einer Wiederholung bleibt immer
    # Regel 114: stumme Zeige-Strecken der Montage (Arbeitsschritte als schnelle Bildfolge). Dort gibt es keine Sprache –
    # was die Erkennung trotzdem „hört“ (Halluzination auf Stille), wird nie Untertitel.
    zeigen = [(float(a), float(b)) for a, b in ((PLAN or {}).get("zeigen") or []) if b - a > 0.2]
    drop |= {w["i"] for w in words if any(a - 0.05 <= w["s"] < b for a, b in zeigen)}
    # Regel 96: Was WÄHREND eines Kameraschwenks nebenbei gesagt wird („lassen es kurz hierhin“), fliegt mit dem Schwenk raus –
    # außer den letzten Wörtern, mit denen der nächste Satz schon beginnt („jetzt zwei Gurken“)
    pan_cuts = []
    for pa, pb in pans():  # auch im sicheren Modus
        inside = [w for w in words if pa <= w["s"] < pb]
        if pb - pa < 1.5:  # Regel 97: kurzer Schwung (z. B. am Anfang einer Einstellung) – nur das Bild kürzen, wenn dabei
            if not [w for w in inside if w["i"] not in drop]:  # niemand etwas sagt; Gesprochenes wird hier nicht geopfert
                pan_cuts.append((pa + 0.05, pb - 0.05))
            continue
        tail_s = pb
        nxt = next((w for w in words if w["s"] >= pb), None)
        for w in reversed(inside):
            ref = nxt["s"] if tail_s == pb and nxt else tail_s
            if w["s"] >= pb - 1.2 and ref - w["s"] <= 2.2:
                tail_s = w["s"]
            else:
                break
        drop |= {w["i"] for w in inside if w["s"] < tail_s}
        pan_cuts.append((pa + 0.1, min(pb, tail_s) - 0.12))
    STATS.update(filler=len(fil), repeats=len(rep), gemini=len(gem), retakes=len(ret), gemini_on=bool(GEMINI_KEY),
                 removed=[w["t"] for w in words if w["i"] in drop][:80])
    kept = [w for w in words if w["i"] not in drop]
    PAD_IN, PAD_OUT, MAX_GAP = (0.08, 0.2, 0.2) if safe else (0.05, 0.1, 0.14)
    ranges = []
    for w in kept:
        tail = PAD_OUT + (0.08 if SENT_END.search(w["t"]) or w["t"].endswith(",") else 0)  # Satzende darf ausklingen
        if w is kept[-1]:  # letztes Wort des Videos: Whisper setzt das Ende oft zu früh („Capri-Son-“) – lieber kurz ausklingen
            tail = max(tail, 0.45)
        s, e = max(0.0, w["s"] - PAD_IN), min(DUR, w["e"] + tail)
        need = min(w["e"] - w["s"], expected_len(w["t"]))  # jedes Wort behält mindestens seine normale Länge
        for a, b in ([] if safe else SIL):  # Whisper dehnt Wörter gern über Pausen – Stille am Rand abschneiden
            if a <= s + 0.04 < b < e:
                s = min(b - PAD_IN, w["e"] - need - PAD_IN)
            if s < a < e - 0.04 <= b and w is not kept[-1]:
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
        # aufeinanderfolgende gestrichene Wörter (Fehlstart „ihr wolltet – warte –“) sind EIN Stück: einzeln war jedes
        # Wort kürzer als die Mindest-Schnittlänge und blieb deshalb drin (Cheesecake-Ordner 03.10.)
        if dropped and k and words[k - 1]["i"] in drop and a - dropped[-1][1] < 0.35:
            dropped[-1] = (dropped[-1][0], max(dropped[-1][1], b))
        else:
            dropped.append((a, b))
    filled = []
    for k, (s, e) in enumerate(ranges):
        if filled:
            gap_a, gap_b = filled[-1][1], s
            cuts_in_gap = sorted([(max(a, gap_a), min(b, gap_b)) for a, b in SIL + GAPS + dropped if b > gap_a and a < gap_b])
            pos, keep_parts = gap_a, []
            for a, b in cuts_in_gap:
                if b - a < 2 * PAUSE_SIDE + 0.12:  # kurze Pause: natürlich lassen, kein Mini-Schnitt
                    continue
                if a - pos > 0.06:
                    keep_parts.append([pos, a + PAUSE_SIDE])
                pos = max(pos, b - PAUSE_SIDE)
            if gap_b - pos > 0.06:
                keep_parts.append([pos, gap_b])
            cuts_in_gap = [(a, b) for a, b in cuts_in_gap if b - a >= 2 * PAUSE_SIDE + 0.12]
            if not cuts_in_gap:  # keine (lange) Stille gemessen → alles behalten
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
            if a > cur + 0.1 and b < e - 0.1 and b - a > 2 * PAUSE_SIDE + 0.15:
                out.append([cur, a + PAUSE_SIDE])
                cur = b - PAUSE_SIDE
        out.append([cur, e])
    out = [r for r in out if r[1] - r[0] > 0.08]
    # Regel 113 (Fehlersuche 06.10., „Ja“ des Kameramanns im Angebote-Video): Ein gestrichenes Wort, das frei zwischen zwei
    # Pausen steht (Zwischenruf, „Ja.“, „Danke.“, „So?“), wurde zwar gestrichen, blieb aber HÖRBAR – das Stück war kürzer
    # als die Mindest-Schnittlänge bzw. wurde mit den Nachbarn wieder verbunden. Jetzt wird es wirklich herausgeschnitten,
    # mit Schutzabstand zu den behaltenen Nachbarwörtern (Wiederholungen mitten im Satz bleiben davon unberührt).
    ks = sorted((w["s"], w["e"]) for w in kept)
    zw = []
    # Nachtrag 06.10. abends (Inhaber: „bei Sekunde 10 hört man wieder das Ja“): Die Erkennung gab dem „Ja“ nur 0,04 s Länge –
    # zu kurz für einen Schnitt, und der Abschnitt davor („So?“) wurde einzeln behandelt. Alle gestrichenen Wörter zwischen
    # denselben zwei behaltenen Nachbarn sind EIN Stück: vom ersten bis zum letzten fällt alles weg, auch die Luft dazwischen.
    grp = {}
    for a, b in dropped:
        if any(a < (s_ + e) / 2 < b for s_, e in ks):
            continue
        prev_e = max((e for s_, e in ks if e <= a + 0.1), default=0.0)
        next_s = min((s_ for s_, e in ks if s_ >= b - 0.1), default=DUR)
        g = grp.setdefault((prev_e, next_s), [a, b])
        g[0], g[1] = min(g[0], a), max(g[1], b)
    for (prev_e, next_s), (a, b) in grp.items():
        c0, c1 = max(a, prev_e + 0.10), min(b, next_s - 0.08)
        if c1 - c0 >= 0.15 and c1 - c0 >= 0.8 * (b - a):
            zw.append((c0, c1))
    if zw:
        mit_wort = lambda r: any(r[0] - 0.05 <= s_ < r[1] for s_, e in ks)
        out = [r for r in subtract(out, zw) if r[1] - r[0] > 0.08 and (r[1] - r[0] >= 0.3 or mit_wort(r))]
        STATS["zwischenrufe_geschnitten"] = len(zw)
    if not safe:
        out = subtract(out, hidden_cuts(kept))
    pan_cuts = [(a, b) for a, b in pan_cuts if b - a >= 0.6]
    if pan_cuts:
        out = [r for r in subtract(out, pan_cuts) if r[1] - r[0] > 0.08]
        STATS["schwenk_cut_s"] = round(sum(b - a for a, b in pan_cuts), 1)
    # Lange Strecken ohne Worte (Essen, Jubel, Warten, Musik): ein Profi zeigt davon ~2 s und springt weiter.
    # Anfang (0,8 s) und Ende (0,5 s) der Strecke bleiben – so bleibt die Handlung sichtbar (Nutzer zeigt Kauen kaum).
    # Unverständliches Gemurmel (Kauen, Nuscheln – ohne Untertitel) zählt dabei wie Stille: sonst bleiben 10 s „mhm“ stehen
    clear = [w for w in kept if not w.get("unclear")]
    # Regel 107 (Inhaber 06.10.: „er sagt ‚jetzt kommt Soße auf den Deckel‘ und dann Cut – man sieht gar nicht, wie er es macht“):
    # In Zubereitungs-/Anleitungs-Videos passiert die Handlung NACH der Ansage. Dort bleibt mehr von der wortlosen Strecke
    # stehen (1,8 s statt 0,8 s) – Kameraschwenks darin sind an dieser Stelle schon herausgeschnitten (Regel 96/97).
    _fmt = ((PLAN or {}).get("format") or (STATS.get("story_format") or {}).get("format") or "").lower()
    nach = 1.8 if re.search(r"zubereit|anleitung|tutorial|rezept|behind|so wird|so entsteht", _fmt) else 0.8
    long_gaps = [(a["e"] + nach, b["s"] - 0.5) for a, b in zip(clear, clear[1:]) if b["s"] - a["e"] > nach + 1.6]
    STATS["handlung_nach_ansage_s"] = nach
    if long_gaps:
        out = [r for r in subtract(out, long_gaps) if r[1] - r[0] > 0.08]
        kept = [w for w in kept if not any(a <= w["s"] and w["e"] <= b for a, b in long_gaps)]  # weggeraffte Wörter
        STATS["long_gaps_cut_s"] = round(sum(y - x for x, y in long_gaps), 1)
    if zeigen:  # Regel 114: Zeige-Strecken bleiben vollständig stehen (keine Stille-/Pausen-Kürzung), Sprache drumherum wie immer
        lo_t = words[0]["s"] - 1.0 if words else 0.0
        hi_t = words[-1]["e"] + 1.0 if words else DUR
        zz = [(a, b) for a, b in zeigen if b > lo_t and a < hi_t]
        out = sorted([r for r in subtract(out, zz) if r[1] - r[0] > 0.08] + [[a + 0.02, b - 0.02] for a, b in zz])
        STATS["zeige_strecken_s"] = round(sum(b - a for a, b in zz), 1)
    for a, b in out:  # Diagnose: lange Abschnitte mit wenig Text (Pausen, die nicht gerafft wurden)
        inside = [w for w in kept if a <= w["s"] < b]
        if b - a > 8:
            log(f"Diagnose: {a:.1f}–{b:.1f} s, {len(inside)} Wörter: " +
                " ".join(f"{w['t']}{'?' if w.get('unclear') else ''}@{w['s']:.1f}" for w in inside)[:900])
    return out, kept


# ─────────────────────────────── 5. Bildausschnitt (Gesicht) ───────────────────────────────
import cv2  # noqa: E402
import numpy as np  # noqa: E402

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
if PREM:  # Kino-Look: etwas mehr Kontrast, weiche Vignette, feine Körnung
    GRADE += ",eq=contrast=1.04,vignette=angle=PI/5,noise=alls=3:allf=t"
log(f"Helligkeit {LUMA:.0f} → {GRADE}")


FACES_OUT = []  # Gesichtslage im fertigen Bild (oben, unten in px) – für die Position der Untertitel


def face_info(t0, t1):
    """Alle Gesichter im Abschnitt: größtes (Mitte x, y, Höhe – 0..1), Anzahl, alle Boxen."""
    res = []
    for f in (0.25, 0.5, 0.75):
        CAPV.set(cv2.CAP_PROP_POS_MSEC, (t0 + (t1 - t0) * f) * 1000)
        ok, fr = CAPV.read()
        if not ok:
            continue
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        sc = 640 / max(g.shape)
        g = cv2.resize(g, None, fx=sc, fy=sc)
        fs = FACE.detectMultiScale(g, 1.1, 5, minSize=(int(min(g.shape) * .05),) * 2)
        if len(fs):
            boxes = [((x + w / 2) / g.shape[1], (y + h / 2) / g.shape[0], h / g.shape[0]) for x, y, w, h in fs]
            res.append((max(boxes, key=lambda b: b[2]), boxes))
    if not res:
        return None
    big = sorted(r[0] for r in res)[len(res) // 2]
    return {"c": big, "n": max(len(r[1]) for r in res), "boxes": [b for r in res for b in r[1]]}


def med(v):
    v = sorted(v)
    return v[len(v) // 2]


_FACE_CACHE = {}


def plan_crops(ranges, zs=1.0, shift=None, pad=None):
    """Bildausschnitt je Abschnitt. Clean: ruhig – gleicher Ausschnitt, kleine Schnitte werden weich überblendet; nur bei
    einem großen Zeitsprung (Pose ändert sich deutlich) ein leichter Zoom-Wechsel, damit der Schnitt gewollt wirkt.
    Viral: Zoom-Punches. Gruppen/Totalen: nie springen. zs = Zusatz-Zoom für die Stabilisierung (Rand zum Ausgleichen).
    shift = mittlere Stabilisierungs-Verschiebung je Abschnitt (Quell-Pixel): Gesichter werden in „ruhigen“ Koordinaten
    geplant – sonst würde der Ausschnitt dem Kamerawackeln UND der Stabilisierung folgen (doppelt, Ruckler am Schnitt)."""
    viral = LOUD
    infos = []
    for k, (s, e) in enumerate(ranges):
        if (s, e) not in _FACE_CACHE:
            _FACE_CACHE[(s, e)] = face_info(s, e)
        i = _FACE_CACHE[(s, e)]
        if i and shift:
            mx, my = shift[k][0] / W, shift[k][1] / H
            i = {"c": (i["c"][0] - mx, i["c"][1] - my, i["c"][2]), "n": i["n"],
                 "boxes": [(bx - mx, by - my, bh) for bx, by, bh in i["boxes"]]}
        infos.append(i)
    found = [i for i in infos if i]
    closeup = bool(found) and sum(1 for i in found if i["n"] == 1 and i["c"][2] > 0.12) >= 0.6 * len(found)
    base = (med([i["c"][0] for i in found]), med([i["c"][1] for i in found]), med([i["c"][2] for i in found])) if found else (0.5, 0.4, 0.0)
    moving = bool(found) and max(i["c"][0] for i in found) - min(i["c"][0] for i in found) > 0.25
    STATS.update(closeup=closeup, faces=round(med([i["n"] for i in found]), 1) if found else 0)
    FACES_OUT.clear()
    crops, prev, zc, t_out = [], None, 1.0, 0.0
    for k, (s, e) in enumerate(ranges):
        gap = s - ranges[k - 1][1] if k else 0.0
        t_a, t_out = t_out, t_out + olen(s, e)
        if viral:
            # Schnitt-Zooms etwas dezenter als früher – die großen Punches sitzen jetzt gezielt auf den Pointen (zoom_punches)
            zoom = [1.0, 1.1, 1.04, 1.14][k % 4] if closeup else [1.0, 1.06][k % 2]
        else:
            if closeup and gap >= BIG_JUMP:
                zc = 1.07 if zc == 1.0 else 1.0
            zoom = zc
        cw, ch = BW / (zoom * zs), BH / (zoom * zs)
        c = base
        if moving and infos[k]:
            # Person bewegt sich: Ausschnitt bleibt stehen, solange das Gesicht bequem im Bild ist (kein Nachruckeln an
            # jedem Schnitt); erst wenn es aus der Mitte wandert, wird am Schnitt neu ausgerichtet
            c = infos[k]["c"]
            if prev is not None:
                if abs(c[0] - prev[0]) * W < 0.2 * cw and abs(c[1] - prev[1]) * H < 0.1 * ch:
                    c = prev
                else:
                    c = tuple(0.8 * a + 0.2 * b for a, b in zip(c, prev))
        prev = c
        # Rand für die Stabilisierung freihalten (Ausschnitt nie ganz an den Bildrand – sonst kann dort nichts ausgeglichen werden)
        px, py = (min(pad[0], (W - cw) / 2), min(pad[1], (H - ch) / 2)) if pad else (0.0, 0.0)
        x = min(max(c[0] * W - cw / 2, px), W - cw - px)
        y = min(max(c[1] * H - ch * 0.36, py), H - ch - py)
        for bx, by, bh in (infos[k]["boxes"] if infos[k] else []):
            cy, fh = (by * H - y) / ch * 1920, bh * H / ch * 1920
            if 0 < cy < 1920:
                FACES_OUT.append((cy - fh * 0.75, cy + fh * 0.85, (bx * W - x) / cw * 1080, t_a, t_out))  # oben, unten, Mitte x, von–bis (Clip-Zeit)
        crops.append({"w": int(cw) // 2 * 2, "h": int(ch) // 2 * 2, "x": int(x) // 2 * 2, "y": int(y) // 2 * 2,
                      "fx": x, "fy": y, "fw": cw, "fh": ch, "zoom": zoom, "c": c})
    return crops


# ─────────────────────────────── 5b. Bildstabilisierung ───────────────────────────────
# Wie „Stabilisieren“ in CapCut/Premiere, aber über Schnitte hinweg: Die Kamerabahn (Verschiebung + Drehung des
# HINTERGRUNDS) wird für den fertigen Clip am Stück berechnet und stark geglättet. Das Bild folgt nur der ruhigen Bahn –
# Handwackeln, leichtes Kippen und der Sprung des Hintergrunds an Jump-Cuts (Kamera ist in der weggeschnittenen Zeit
# verrutscht) verschwinden. Alles subpixel-genau (kein Stufen-Ruckeln wie bei ganzzahligem Zuschneiden).
STAB_RATE = min(30.0, FPS)
STAB_SIGMA = 2.6                   # Glättung (s): wie vom Stativ/Gimbal – Nutzer: „da darf nichts wackeln“ (Regel 92)
STAB_ZOOM_MAX = 1.20               # bestimmt nur noch, wie weit ausgeglichen wird (± ~7 % des Bildes) – gezoomt wird NICHT (Regel 93)
TRACK_W = 320


def _bg_mask(g):
    """Gesichter und Körper ausblenden – verfolgt wird nur der Hintergrund (sonst würde Bewegung „wegstabilisiert“, z. B.
    ein gemustertes Hemd). Gesichter auf einem größeren Bild suchen; wird keins gefunden, die Bildmitte ausblenden
    (dort steht fast immer die Person)."""
    sh, sw = g.shape
    mask = np.full(g.shape, 255, np.uint8)
    big = cv2.resize(g, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_LINEAR)
    faces = FACE.detectMultiScale(big, 1.1, 4, minSize=(int(sw * .07),) * 2)
    for fx, fy, fw, fh in faces:
        fx, fy, fw, fh = fx / 2, fy / 2, fw / 2, fh / 2
        cv2.rectangle(mask, (int(fx - 1.8 * fw), int(fy - 0.9 * fh)), (int(fx + 2.8 * fw), sh), 0, -1)
    return mask  # kein Gesicht (B-Roll, Nahaufnahme): ganzes Bild verfolgen – RANSAC ignoriert bewegte Hände/Dinge


def _motion(a, b, mask, far=False):
    """Bewegung des Hintergrunds von Bild a nach b: (dx, dy der Bildmitte in Quell-Pixeln, Drehung in Rad) oder None."""
    p0 = cv2.goodFeaturesToTrack(a, 250, 0.005, 6, mask=mask)
    if p0 is None or len(p0) < 15:
        return None
    win, lvl = ((31, 31), 4) if far else ((21, 21), 3)
    p1, st, _ = cv2.calcOpticalFlowPyrLK(a, b, p0, None, winSize=win, maxLevel=lvl)
    ok = st.ravel() == 1
    if ok.sum() < 15:
        return None
    m, inl = cv2.estimateAffinePartial2D(p0[ok], p1[ok], method=cv2.RANSAC, ransacReprojThreshold=0.8 if not far else 1.5)
    if m is None or inl is None or inl.sum() < (25 if far else 12) or (far and inl.sum() < 0.4 * ok.sum()):
        return None
    sh, sw = a.shape
    ang = float(np.arctan2(m[1, 0], m[0, 0]))
    cx, cy = sw / 2, sh / 2
    dx = m[0, 0] * cx + m[0, 1] * cy + m[0, 2] - cx
    dy = m[1, 0] * cx + m[1, 1] * cy + m[1, 2] - cy
    if abs(ang) > (0.05 if far else 0.02):
        return None
    return dx * W / sw, dy * W / sw, ang


CUTS_FOUND = []  # Zeiten harter Schnitte der zuletzt verfolgten Strecke


def _track(t0, t1):
    """Kamerabahn zwischen t0 und t1 (STAB_RATE Bilder/s): kumuliert (x, y, Winkel) – Hintergrund, Personen ausgeblendet."""
    SW = TRACK_W
    SH = int(H * SW / W) // 2 * 2
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-to", f"{t1:.3f}", "-i", A.src, "-vf",
                             f"fps={STAB_RATE:.3f},scale={SW}:{SH},format=gray", "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    prev, mask, path, n = None, None, [], 0
    cur = np.zeros(3)
    CUTS_FOUND.clear()
    while True:
        buf = proc.stdout.read(SW * SH)
        if len(buf) < SW * SH:
            break
        g = np.frombuffer(buf, np.uint8).reshape(SH, SW)
        if n % 10 == 0:
            mask = _bg_mask(g)
        n += 1
        if prev is not None:
            d = _motion(prev, g, mask)
            if d is not None:
                cur = cur + d
            elif float(np.abs(g.astype(np.int16) - prev).mean()) > 22:  # harter Schnitt im Material (z. B. Zwischenschnitt)
                CUTS_FOUND.append(t0 + (n - 1) / STAB_RATE)
        path.append(cur.copy())
        prev = g
    proc.wait()
    return np.array(path, dtype=float).reshape(-1, 3)


def _small(t):
    CAPV.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000)
    ok, fr = CAPV.read()
    if not ok:
        return None
    g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
    return cv2.resize(g, (TRACK_W, int(H * TRACK_W / W) // 2 * 2), interpolation=cv2.INTER_AREA)


def _gauss(a, sig):
    """Gauß-Glättung entlang der Zeit."""
    r = max(1, int(3 * sig))
    ker = np.exp(-0.5 * (np.arange(-r, r + 1) / max(sig, 1e-3)) ** 2)
    ker /= ker.sum()
    # Ränder punktgespiegelt fortsetzen: eine gewollte Kamerafahrt am Anfang/Ende bleibt erhalten (kein Nachziehen)
    return np.stack([np.convolve(np.pad(a[:, i], r, mode="reflect", reflect_type="odd"), ker, "valid")
                     for i in range(a.shape[1])], 1)


SPEED = [1.0]  # Abspieltempo des fertigen Clips (1,0 = Original)


def olen(s, e):
    """Länge eines Abschnitts im FERTIGEN Clip (Quellzeit ÷ Tempo)."""
    return (e - s) / SPEED[0]


def choose_speed(kept):
    """Tempo wie der Nutzer: Er beschleunigt Umfragen im Mittel ×1,27 (20 Vorher-Nachher-Paare) – fertig wird mit ≈ 6,3 Silben/s
    gesprochen (Rohmaterial ≈ 5,5). Ziel je Stil; schnelle Sprecher werden nicht weiter beschleunigt; Tonhöhe bleibt (atempo)."""
    syl = lambda t: max(1, len(re.findall(r"[aeiouäöüy]+", t.lower())))
    n = d = 0.0
    for a, b in zip(kept, kept[1:]):
        if b["s"] - a["e"] < 0.3:
            n += syl(a["t"])
            d += b["s"] - a["s"]
    rate = n / d if d > 4.0 else 0.0  # zu wenig zusammenhängende Sprache → Messung unsicher → Originaltempo
    target, mx = (6.6, 1.3) if LOUD else (6.2, 1.2)
    # Rückmeldung 05.10. (Regel 80): Ordner-, Zubereitungs- und Erklär-Videos nur leicht beschleunigen – die Malo's
    # Junior Box war mit ×1,3 „zu schnell“. Umfragen bleiben knackig wie beim Nutzer.
    fmt = ((PLAN or {}).get("format") or (STATS.get("story_format") or {}).get("format") or "").lower()
    if PLAN or re.search(r"zubereit|anleitung|tutorial|erklär|vorstell|promo|rezept|food|behind", fmt):
        target, mx = 5.8, 1.1
    f = min(mx, max(1.0, target / rate)) if rate else 1.0
    f = round(f * 20) / 20
    SPEED[0] = f if f >= 1.05 else 1.0
    STATS.update(speech_rate=round(rate, 1), speed=SPEED[0])
    if SPEED[0] > 1.0:
        log(f"Tempo: {rate:.1f} Silben/s → ×{SPEED[0]:.2f} (wie der Nutzer: knackig, Tonhöhe bleibt)")
    return SPEED[0]


def seg_frames(ranges):
    """Anzahl Bilder je Abschnitt im fertigen Clip (aufaddiert gerundet – Bild und Ton bleiben exakt gleich lang)."""
    out, acc = [], 0.0
    for s, e in ranges:
        a = int(round(acc * OF))
        acc += olen(s, e)
        out.append(max(1, int(round(acc * OF)) - a))
    return out


def plan_stab(ranges):
    """Rückgabe None (ruhiges Stativbild) oder dict: corr[k] = Korrektur (x, y, Winkel) je Ausgabebild von Abschnitt k,
    ext(k, t) = Korrektur für Überblend-Bilder hinter dem Abschnitt, mean[k] = mittlere Verschiebung (für die Planung)."""
    blocks = []  # nahe beieinander liegende Abschnitte am Stück verfolgen – der Sprung am Schnitt ist dann genau bekannt
    for k, (s, e) in enumerate(ranges):
        if blocks and s - ranges[k - 1][1] < 2.0:
            blocks[-1]["ks"].append(k)
        else:
            blocks.append({"ks": [k]})
    for b in blocks:
        b["t0"] = max(0.0, ranges[b["ks"][0]][0] - 0.1)
        b["path"] = _track(b["t0"], min(DUR, ranges[b["ks"][-1]][1] + 0.4))
        b["cuts"] = sorted(CUTS_FOUND)
    nf = seg_frames(ranges)
    seq, scene, off, breaks = [], [], np.zeros(3), 0
    P = {}

    def pos(k, t):
        b = P[k]
        p = b["path"]
        if not len(p):
            return np.zeros(3)
        i = min(max((t - b["t0"]) * STAB_RATE, 0.0), len(p) - 1.0)
        j = int(i)
        f = i - j
        return p[j] * (1 - f) + p[min(j + 1, len(p) - 1)] * f
    sid = 0
    for bi, b in enumerate(blocks):
        for k in b["ks"]:
            P[k] = b
        k0 = b["ks"][0]
        if bi:
            # neuer Block (große Lücke): Sprung des Hintergrunds direkt zwischen letztem und nächstem Bild messen
            kp = k0 - 1
            fa, fb = _small(ranges[kp][1] - 1 / OF), _small(ranges[k0][0])
            d = _motion(fa, fb, cv2.bitwise_and(_bg_mask(fa), _bg_mask(fb)), far=True) if fa is not None and fb is not None else None
            last = pos(kp, ranges[kp][1] - 1 / OF) + off
            if d is None:  # anderer Ort/Blickwinkel → neue Szene, nichts angleichen
                sid += 1
                breaks += 1
                off = last - pos(k0, ranges[k0][0])
            else:
                off = last + np.array(d) - pos(k0, ranges[k0][0])
        for k in b["ks"]:
            s = ranges[k][0]
            for i in range(nf[k]):
                tt = s + i * SPEED[0] / OF
                # Regel 92: jede Einstellung für sich beruhigen – sonst zieht die Glättung das Wackeln der einen
                # Einstellung über den Schnitt in die nächste („an den Übergängen wackelt es“)
                while b["cuts"] and b["cuts"][0] <= tt + 0.5 / STAB_RATE:
                    b["cuts"].pop(0)
                    sid += 1
                    breaks += 1
                seq.append(pos(k, tt) + off)
                scene.append(sid)
            b.setdefault("off", {})[k] = off.copy()
    Q = np.array(seq)
    scene = np.array(scene)
    # Ruhige Bahn in einem „Schlauch“ um die echte Bahn: so glatt wie möglich, aber nie weiter weg, als der Rand des
    # Ausschnitts hergibt (sonst stößt die Korrektur an den Bildrand und es ruckelt trotzdem)
    zl = STAB_ZOOM_MAX - 0.02
    lim = np.array([0.8 * W * (1 - 1 / zl) / 2 if BW >= W - 2 else 0.12 * BW, 0.8 * H * (1 - 1 / zl) / 2, 0.02])
    T = np.zeros_like(Q)
    sig = STAB_SIGMA * OF
    for sc in np.unique(scene):
        m = scene == sc
        q = Q[m]
        t = _gauss(q, sig)
        for _ in range(25):
            t = _gauss(np.clip(t, q - lim, q + lim), sig / 3)
        T[m] = _gauss(np.clip(t, q - lim, q + lim), 0.08 * OF)
    C = np.clip(Q - T, -lim * 1.05, lim * 1.05)
    amp = np.hypot(C[:, 0], C[:, 1]) * 1920 / BH
    raw = Q - _gauss(Q, 0.12 * OF)  # schnelles Zittern (nur für das Protokoll)
    STATS["shake"] = round(float(np.sqrt((np.hypot(raw[:, 0], raw[:, 1]) ** 2).mean())) * 1920 / BH, 1)
    STATS["stab_corr_p95"] = round(float(np.percentile(amp, 95)), 1)
    STATS["stab_breaks"] = breaks
    if np.percentile(amp, 95) < 1.2 and np.degrees(np.abs(C[:, 2]).max()) < 0.15:
        log(f"Stabilisierung: Bild ist ruhig (Korrektur < 1,2 px) – nicht nötig")
        return None
    corr, mean, a = [], [], 0
    for k in range(len(ranges)):
        corr.append(C[a:a + nf[k]])
        mean.append(tuple(C[a:a + nf[k], :2].mean(0)))
        a += nf[k]

    def ext(k, t):
        return pos(k, t) + P[k]["off"][k] - T[sum(nf[:k + 1]) - 1]
    log(f"Stabilisierung: Korrektur p95 {STATS['stab_corr_p95']} px, Zittern {STATS['shake']} px, {breaks} Szenenwechsel")
    pad = np.percentile(np.abs(C[:, :2]), 98, axis=0) + 2.0  # so viel Rand braucht der Ausschnitt je Seite
    return {"corr": corr, "ext": ext, "mean": mean, "lim": tuple(pad)}


def stab_zoom(stab, crops):
    """Zusatz-Zoom, damit der verschobene/gedrehte Ausschnitt fast immer im Bild bleibt (97 % der Bilder)."""
    need = []
    for k, c in enumerate(crops):
        z = c["zoom"]
        for cx, cy, a in stab["corr"][k][::3]:
            sa, ca = abs(np.sin(a)), np.cos(a)
            ny = (BH * ca + BW * sa) / (z * max(1.0, H - 2 * abs(cy)))
            nx = (BW * ca + BH * sa) / (z * max(1.0, W - 2 * abs(cx))) if BW >= W - 2 else 0.0
            need.append(max(ny, nx))
    zs = float(min(STAB_ZOOM_MAX, max(1.0, np.percentile(need, 97) + 0.003))) if need else 1.0
    STATS["stab_zoom"] = round(zs, 3)
    return zs


# ─────────────────────────────── 6. Untertitel (ASS) ───────────────────────────────
def ass_color(hex_):
    h = hex_.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


ACC, WHITE, BLACK = ass_color(A.color), "&H00FFFFFF", "&H00000000"
# wie die Vorschau auf der Website: Schrift, Größe, Kontur, Wörter pro Einblendung
CAP = {
    "rise":    dict(font="Poppins SemiBold", size=150, bold=0, ital=0, bord=0, shad=5, back="&H80000000", n=4, chars=16, case="lower", pop=False, blur=0, file="Poppins-normal-600.ttf", k=0.564),
    "clean":   dict(font="Montserrat", size=138, bold=1, ital=0, bord=10, shad=0, back=BLACK, n=4, chars=14, case=None, pop=True, blur=0, file="Montserrat-Bold.otf", k=0.643),
    "hormozi": dict(font="Montserrat Thin Black", size=160, bold=0, ital=0, bord=13, shad=6, back=BLACK, n=3, chars=11, case="upper", pop=True, blur=0, file="Montserrat-normal-900.ttf", k=0.637),
    "neon":    dict(font="Bebas Neue", size=230, bold=0, ital=0, bord=5, shad=0, back=BLACK, n=3, chars=14, case="upper", pop=False, blur=2, glow=True, file="BebasNeue-normal-400.ttf", k=0.763),
    "comic":   dict(font="Bangers", size=220, bold=0, ital=0, bord=12, shad=8, back=BLACK, n=2, chars=10, case="upper", pop=True, blur=0, file="Bangers-normal-400.ttf", k=0.568),
    # Inhaber 07.10.: „zu wenige Untertitel zur Auswahl“ – vier weitere Stile aus den vorhandenen Schriften
    "bold":    dict(font="Poppins ExtraBold", size=150, bold=0, ital=0, bord=10, shad=0, back=BLACK, n=3, chars=13, case="upper", pop=True, blur=0, file="Poppins-normal-800.ttf", k=0.568),
    "box":     dict(font="Montserrat", size=120, bold=1, ital=0, bord=14, shad=0, back=BLACK, n=3, chars=14, case=None, pop=False, blur=0, box=True, file="Montserrat-Bold.otf", k=0.647),
    "impact":  dict(font="Bebas Neue", size=220, bold=0, ital=0, bord=10, shad=7, back=BLACK, n=3, chars=14, case="upper", pop=True, blur=0, file="BebasNeue-normal-400.ttf", k=0.769),
    "mini":    dict(font="Montserrat", size=100, bold=1, ital=0, bord=7, shad=0, back=BLACK, n=5, chars=20, case=None, pop=False, blur=0, file="Montserrat-Bold.otf", k=0.647),
    # „Wide“: breite, edle Großbuchstaben wie im Premium-Beispiel der Website (aus Montserrat Black, in die Breite gezogen)
    "wide":    dict(font="Montserrat Thin Black", size=100, bold=0, ital=0, bord=3, shad=5, back="&H80000000", n=2, chars=12, case="upper", pop=False, blur=0, sx=128, file="Montserrat-normal-900.ttf", k=0.815),
    "serif":   dict(font="Playfair Display", size=158, bold=1, ital=1, bord=0, shad=5, back="&H80000000", n=4, chars=16, case=None, pop=False, blur=0, file="PlayfairDisplay-italic-700.ttf", k=0.707),
}
CS = CAP.get(A.caption, CAP["rise"])
# Regel 112 (Nutzer 06.10., ausgemessen an seinen Bildschirmfotos von TikTok und Instagram, umgerechnet auf 1080×1920):
# TikTok schneidet auf hohen Handys links/rechts je ~48 px ab, Instagram ~97 px. Oben liegen Suchleiste/Reiter (TikTok bis
# ~230, Instagram bis ~200), rechts die Knopfspalte (TikTok ab x≈900 und y≈780, Instagram ab x≈880 und y≈910), unten
# Profil/Beschreibung (Instagram ab ~1406, TikTok ab ~1675). Alles, was wir einblenden, bleibt in diesem Rahmen.
SAFE_T, SAFE_B, SAFE_L, SAFE_R = 240, 1400, 110, 970
SAFE_BTN_Y, SAFE_BTN_X = 770, 870   # ab dieser Höhe endet der freie Bereich rechts schon vor der Knopfspalte
CAP_MAXW = 660   # max. Zeilenbreite (px): mittig 210–870 → endet vor der Knopfspalte von TikTok/Reels
LAY = {}         # Platzvergabe des aktuellen Clips (plan_layout): cy, hy, title, lx, top, rects
CAPFS = [CS["size"]]  # Schriftgröße dieses Videos (caption_groups passt sie an)
_CAPFONT = []


def cap_w(txt, fs):
    """Breite von txt in der Untertitel-Schrift bei Größe fs – so wie libass sie setzt (Faktor k je Schrift gemessen)."""
    try:
        if not _CAPFONT:
            from PIL import ImageFont
            _CAPFONT.append(ImageFont.truetype(os.path.join(FONTS, CS["file"]), 100))
        return _CAPFONT[0].getlength(txt) * CS["k"] * fs / 100 + len(txt)
    except Exception:  # noqa: BLE001
        return 0.55 * fs * len(txt)


def is_emph(t):
    c = norm(t)
    return bool(re.search(r"\d", t)) or (len(c) >= 9 and t[:1].isupper()) or c in {"nie", "immer", "niemand", "alles", "nichts", "krass"}


def ass_time(t):
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def fmt_word(t):
    t = t.replace("{", "(").replace("}", ")")
    t = re.sub(r"(?<=\w[.,!?…])[\-–—]+$|(?<=\w)[\-–—]+$", "", t)  # abgebrochene Wörter: „Black.-“ → „Black.“
    t = re.sub(r"([.,!?])[.,]+$", r"\1", t)  # „Tandoorichicken..“ → „Tandoorichicken.“
    if CS["case"] == "upper":
        t = t.upper()
    elif CS["case"] == "lower":
        t = t.lower()
    return re.sub(r"[.,;:]+$", "", t) if CS["case"] else t


def face_box(t0=None, t1=None):
    """Regel 86 (Kollege des Nutzers, 05.10.): über das Gesicht kommt NIE etwas – weder Untertitel, Emojis, Titel, Logo,
    Sticker noch Motion Graphics. Bereich, in dem das Gesicht im fertigen Bild vorkommt – über den ganzen Clip (10.–90.
    Perzentil, auch wenn sich die Person bewegt) plus Rand. (oben, unten, links, rechts) oder None."""
    # t0/t1 (Regel 112): nur die Gesichter, die in diesem Zeitfenster des fertigen Clips zu sehen sind – für alles, was nur
    # kurz eingeblendet wird (Titel 0–3,4 s, Emojis). Sonst sperrt bei mehreren Personen/Einstellungen das ganze Bild.
    fo = FACES_OUT if t0 is None else [f for f in FACES_OUT if len(f) < 5 or (f[3] < t1 and f[4] > t0)]
    if not fo:
        return None
    q = lambda v, p: sorted(v)[min(len(v) - 1, int(p * len(v)))]
    if t0 is not None:  # kurzes Fenster: jedes Gesicht zählt ganz (kein Abschneiden von Ausreißern)
        q = lambda v, p: min(v) if p < 0.5 else max(v)
    ft, fb = q([f[0] for f in fo], 0.1) - 20, q([f[1] for f in fo], 0.9) + 20
    xs = [f[2] for f in fo if len(f) > 2] or [540]
    fw = (fb - ft) * 0.8
    return max(0, ft), min(1920, fb), max(0, q(xs, 0.1) - fw / 2 - 30), min(1080, q(xs, 0.9) + fw / 2 + 30)


def hits_face(x0, y0, x1, y1, t0=None, t1=None):
    fbx = face_box(t0, t1)
    return bool(fbx) and x0 < fbx[3] and x1 > fbx[2] and y0 < fbx[1] and y1 > fbx[0]


def emoji_gap():
    """Mindestabstand Emoji-Mitte ↔ Untertitel-Mitte: halbe echte Schrifthöhe + halbes Emoji + Luft."""
    return int(CAPFS[0] * CS["k"] * 0.62) + 95


def _cut(a0, a1, b0, b1):
    return a0 < b1 and a1 > b0


def plan_layout(card=None, logo=False, force=False):
    """Regel 112: EINE Platzvergabe für alles, was eingeblendet wird. Jedes Element bekommt seinen eigenen Platz:
    Logo oben links, Titel oben, Untertitel im unteren Bereich, Emoji direkt unter (sonst über) den Untertiteln.
    Nichts liegt übereinander, nichts über dem Gesicht, nichts unter den Knöpfen/Leisten von TikTok und Instagram.
    card = (Breite, Höhe) des Titels oder None. Passt der Titel nirgends hin, entfällt er (lieber kein Titel als einer
    über dem Gesicht oder auf den Untertiteln). Ergebnis in LAY."""
    half = CAPFS[0] * 0.6
    fbx = face_box()
    lx = SAFE_L
    if logo and hits_face(lx, SAFE_T, lx + LOGO_PX, SAFE_T + LOGO_PX):  # Regel 86: Logo nach rechts, wenn links das Gesicht ist
        lx = SAFE_R - LOGO_PX
        if hits_face(lx, SAFE_T, lx + LOGO_PX, SAFE_T + LOGO_PX):  # auch rechts das Gesicht → Logo entfällt in diesem Video
            logo = False
            STATS["logo_weggelassen"] = True
            log("Logo weggelassen – oben ist in beiden Ecken das Gesicht (Regel 112)")
    top = SAFE_T + (LOGO_PX + 14 if logo else 0)
    lo, hi = top + half, SAFE_B - half
    std = min(1250, SAFE_B - 85 - emoji_gap())  # so hoch, dass ein Emoji darunter noch über der Beschreibung bleibt
    note = None
    if not fbx or fbx[1] < std - half or fbx[0] > std + half:
        cy = std
    elif fbx[1] + 40 + half <= hi:      # unter dem Kinn ist Platz
        cy = fbx[1] + 40 + half
    elif fbx[1] + 10 + half <= hi:
        cy = hi
    elif fbx[0] - half - 60 >= lo:      # sonst über den Kopf
        cy = fbx[0] - half - 60
    elif fbx[0] - half - 15 >= lo:
        cy = lo
    elif fbx[0] + 0.14 * (fbx[1] - fbx[0]) - half >= lo:
        # Test des Inhabers 07.10. (sehr nahes Selfie, das Gesicht füllt die unteren zwei Drittel): Die Untertitel lagen „ganz
        # unten“ quer über Nase und Mund. Über dem Kopf ist dann der bessere Platz – die Zeile darf dabei höchstens den
        # obersten Rand des Gesichtsfelds berühren (Haare/Stirnansatz), nie Augen, Nase oder Mund.
        cy, note = lo, "sehr nahes Gesicht – Untertitel über dem Kopf (am Haaransatz)"
    else:                               # Gesicht füllt den ganzen freien Bereich → ganz unten (Kinn/Hals statt Augen)
        cy, note = hi, "kein freier Platz neben dem Gesicht – Untertitel ganz unten"
    hy, title = top, bool(card)
    if card:
        rand = 30 if not PREM else 0   # die Titel-Karte hat rundum 30 px weichen Schatten – es zählt die sichtbare Karte
        cw, ch = card[0] - 2 * rand, card[1] - 2 * rand
        x0, x1 = (1080 - cw) / 2, (1080 + cw) / 2
        tbx = face_box(0.0, 3.4) if FACES_OUT and len(FACES_OUT[0]) >= 5 else fbx  # der Titel steht nur die ersten 3,4 s
        free = lambda y: (not tbx or not (x0 < tbx[3] and x1 > tbx[2] and _cut(y, y + ch, tbx[0], tbx[1]))) \
            and not _cut(y, y + ch, cy - half - 15, cy + half + 15) and top <= y and y + ch <= SAFE_B
        cand = [top]
        if tbx:
            cand += [tbx[0] - ch - 20, tbx[1] + 21]
        cand += [cy - half - 20 - ch]  # direkt über den Untertiteln; darunter nie (dort stehen die Emojis)
        ok = [y for y in cand if free(y)]
        if ok:
            hy = int(ok[0]) - rand
        elif fbx and cy < fbx[0] + 0.14 * (fbx[1] - fbx[0]) and top + ch + 12 + 2 * half <= fbx[0] + 0.14 * (fbx[1] - fbx[0]):
            hy, cy = top - rand, top + ch + 12 + half   # Untertitel stehen über dem Kopf: Titel ganz oben, Untertitel direkt darunter
        elif force and not _cut(top, top + ch, cy - half - 15, cy + half + 15):
            # Inhaber 06.10. abends: „Die Caption kannst du drin lassen.“ Der Titel entfällt nicht mehr – findet sich kein
            # ganz freier Platz, steht er (einzeilig, flach, nur 3 s) ganz oben am Rand: dort trifft er höchstens Haare/Stirn.
            hy = top - rand
            STATS["titel_am_oberen_rand"] = True
            log("Titel steht ganz oben am Rand – kein völlig freier Platz neben dem Gesicht")
        else:
            title = False
            STATS["titel_weggelassen"] = True
            log("Titel weggelassen – kein Platz, ohne Gesicht oder Untertitel zu verdecken (Regel 112)")
    if note:
        STATS["layout_hinweis"] = note
        log(f"Platzvergabe: {note}")
    LAY.clear()
    LAY.update(cy=int(cy), hy=int(hy), title=title, lx=int(lx), top=int(top), logo=bool(logo),
               trect=(x0, hy + rand, x1, hy + rand + ch) if card and title else None)
    STATS["layout"] = dict(untertitel_y=int(cy), titel_y=int(hy) if title else None, gesicht=[int(v) for v in fbx] if fbx else None)
    return LAY


def emoji_spot(t0, t1):
    """Platz für ein Emoji (Mitte x, y) oder None: unter den Untertiteln, sonst darüber (auch leicht versetzt) –
    nie auf Untertitel, Titel (solange er steht), Logo oder Gesicht und immer im freien Rahmen der Apps."""
    cy, gap, r = caption_y(), emoji_gap(), EMOJI_PX // 2 + 10
    fbx = face_box(t0, t1)
    # Inhaber 07.10.: „Emojis nicht links und rechts vom Gesicht, sondern beim Untertitel – drunter, drüber, drumherum.“
    cand = [(540, cy + gap), (540, cy - gap), (330, cy + gap), (750, cy + gap), (330, cy - gap), (750, cy - gap)]
    for x, y in cand:
        xr = SAFE_BTN_X if y + r > SAFE_BTN_Y else SAFE_R
        if x - r < SAFE_L or x + r > xr or y - r < LAY["top"] or y + r > SAFE_B or abs(y - cy) < gap:
            continue
        if hits_face(x - r, y - r, x + r, y + r, t0, t1):
            continue
        tr = LAY.get("trect")
        if tr and t0 < 3.4 and _cut(x - r, x + r, tr[0], tr[2]) and _cut(y - r, y + r, tr[1] - 10, tr[3] + 10):
            continue
        if LAY.get("logo") and _cut(x - r, x + r, LAY["lx"], LAY["lx"] + LOGO_PX) and _cut(y - r, y + r, SAFE_T, SAFE_T + LOGO_PX):
            continue
        return int(x), int(y)
    return None


def caption_y():
    """Mitte der Untertitel-Zeile – kommt aus der gemeinsamen Platzvergabe (plan_layout, Regel 112)."""
    if "cy" not in LAY:
        plan_layout(None, False)
    return LAY["cy"]


def cap_gap(fs):
    """Wortabstand: Leerzeichen + Kontur (die dicke Umrandung frisst sonst die Lücke: „istrichtig“)."""
    return cap_w(" ", fs) + CS["bord"] * 0.8 + fs * 0.04


def _groups(words_out, fs, stats=None):
    """Einblendungen (Wortgruppen) nach ECHTER Breite: nie breiter als CAP_MAXW, nie zweizeilig; neue Gruppe nach
    Satzzeichen und nach einer Sprechpause (Untertitel laufen im Rhythmus der Sprache)."""
    groups, cur, wide = [], [], 0
    sp = cap_gap(fs)
    for w in words_out:
        if cur:
            wid = sum(cap_w(fmt_word(x[0]), fs) for x in cur) + sp * len(cur) + cap_w(fmt_word(w[0]), fs)
            if len(cur) >= CS["n"] or wid > CAP_MAXW or w[1] - cur[-1][2] > 0.45:
                wide += wid > CAP_MAXW and len(cur) < CS["n"] and w[1] - cur[-1][2] <= 0.45
                groups.append(cur)
                cur = []
        cur.append(w)
        if re.search(r"[.!?,;:…]$", w[0]):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    if stats is not None:
        stats.append(wide)
    return groups


def caption_groups(words_out):
    """Schriftgröße je Video: so groß wie der Stil vorsieht – nur wenn lange Wörter (Deutsch!) die Zeilen oft sprengen
    (Wörter würden dann ständig einzeln aufblitzen), etwas kleiner. Ergebnis in CAPFS."""
    best = None
    for f in (1.0, 0.94, 0.88, 0.84):
        fs = int(CS["size"] * f)
        st = []
        gs = _groups(words_out, fs, st)
        best = (fs, gs)
        if st[0] <= 0.15 * len(gs):  # höchstens jede 7. Zeile wegen Platzmangel umgebrochen
            break
    CAPFS[0] = best[0]
    STATS["caption_size"] = best[0]
    return best[1]


def build_ass(words_out, path, hook=None, extra=(), hide=()):
    """words_out: [(text, start, end)] in Ausgabe-Zeit. Jedes Wort steht an einer FESTEN Stelle (einzeln positioniert):
    Wenn das gesprochene Wort hervorgehoben wird, verrutscht nichts – die Zeile wackelt nicht mehr."""
    groups = caption_groups(words_out)
    fs0 = CAPFS[0]
    head = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{CS['font']},{fs0},{WHITE},{ACC},{ACC if CS.get('glow') else BLACK},{CS['back']},{CS['bold']},{CS['ital']},0,0,"
        f"{CS.get('sx', 100)},100,1,0,{3 if CS.get('box') else 1},{CS['bord']},{CS['shad']},5,0,0,0,1",
        f"Style: Hook,Montserrat Thin Black,82,{BLACK},{ACC},{WHITE},&H00000000,0,0,0,0,100,100,0,0,3,20,0,8,90,90,200,1",
        f"Style: Pop,Montserrat Thin Black,150,{WHITE},{ACC},{BLACK},&H00000000,0,0,0,0,100,100,1,0,1,0,0,5,40,40,0,1",
        f"Style: PopSmall,Montserrat,58,{WHITE},{ACC},{BLACK},&H00000000,1,0,0,0,100,100,1,0,1,0,0,5,40,40,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    y = caption_y()
    blur = f"\\blur{CS['blur']}" if CS["blur"] else ""
    pop = LOUD and CS["pop"]
    dim = A.caption in ("rise", "serif")
    ev = []
    for gi, g in enumerate(groups):
        if any(a <= w[1] < b for w in g for a, b in hide):
            continue  # großes Schlüsselwort/Zähler steht gerade im Bild → diese Untertitel-Zeile ganz weglassen (nie halbe Zeilen)
        txts = [fmt_word(w[0]) for w in g]
        fs = fs0
        sp = cap_gap(fs)
        tot = sum(cap_w(t, fs) for t in txts) + sp * (len(g) - 1)
        if tot > CAP_MAXW:  # einzelnes Riesenwort → nur diese Zeile kleiner
            fs = max(int(fs0 * 0.6), int(fs * CAP_MAXW / tot))
            sp = cap_gap(fs)
            tot = sum(cap_w(t, fs) for t in txts) + sp * (len(g) - 1)
        xs, x = [], 540 - tot / 2
        for t in txts:
            wd = cap_w(t, fs)
            xs.append(x + wd / 2)
            x += wd + sp
        fstag = f"\\fs{fs}" if fs != fs0 else ""
        nxt = groups[gi + 1][0][1] if gi + 1 < len(groups) else None
        g_end = nxt if nxt is not None and nxt - g[-1][2] < 0.7 else g[-1][2] + 0.3
        shown_until = None
        for k, (_, s, e_w) in enumerate(g):
            e_show = g[k + 1][1] if k + 1 < len(g) else g_end
            e_show = min(e_show, max(e_w, s + 0.2) + 1.2)  # nie ewig stehen lassen, wenn das nächste Wort erst viel später kommt
            e_show = min([e_show] + [a for a, b in hide if s < a < e_show])
            if e_show - s < 0.02:
                continue
            appear = shown_until is None or s - shown_until > 0.02  # Zeile erscheint (neu) → sanft einblenden
            shown_until = e_show
            for j, t in enumerate(txts):
                tag = f"\\an5{fstag}{blur}"
                if appear and pop:  # ganze Zeile poppt kurz auf (alle Wörter wachsen gemeinsam aus der Mitte)
                    x0 = 540 + (xs[j] - 540) * 0.86
                    tag += f"\\move({x0:.1f},{y},{xs[j]:.1f},{y},0,110)\\fscx86\\fscy86\\t(0,110,\\fscx100\\fscy100)"
                else:
                    tag += f"\\pos({xs[j]:.1f},{y})" + ("\\fad(70,0)" if appear else "")
                if j == k:
                    tag += "\\1c" + ACC
                    if pop:  # nur dieses Wort wächst minimal – um seine eigene Mitte, die Nachbarn bleiben stehen
                        tag += "\\t(0,90,\\fscx109\\fscy109)\\t(90,170,\\fscx105\\fscy105)" if not appear else \
                               "\\t(110,180,\\fscx105\\fscy105)"
                elif j > k and dim:  # kommt gleich – schon sichtbar, leicht gedimmt
                    tag += "\\alpha&H55&"
                elif j < k and is_emph(g[j][0]) and A.caption in ("rise", "neon", "comic", "serif"):
                    tag += "\\1c" + ACC
                ev.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e_show)},Cap,,0,0,0,,{{{tag}}}{t}")
    if hook:
        hook_txt = re.sub(r"[^\w\s&?!.,:'’\-]", "", hook).strip()  # Emojis kann die Titelschrift nicht – die kommen als Bild
        ev.append(f"Dialogue: 1,{ass_time(0)},{ass_time(3.2)},Hook,,0,0,0,,{{\\fad(120,200)}}{hook_txt.upper()}")
    open(path, "w", encoding="utf-8").write("\n".join(head + ev + list(extra)) + "\n")


# ─────────────────────────────── 7. Rendern ───────────────────────────────
XF = 0.025  # Ton-Überblendung an jedem Schnitt (kein Knacken, kein harter Abriss)
OF = min(FPS, 60.0)  # Bildrate des fertigen Clips
BIG_JUMP = 1.2  # ab so viel entfernter Zeit ändert sich die Pose sichtbar → Clean setzt einen leichten Zoom-Wechsel


def tidy(ranges):
    """Schnitte bildgenau setzen und Mini-Abschnitte (< 0,3 s, wirken wie Ruckler) mit dem Nachbarn verbinden."""
    snap = lambda t: round(t * OF) / OF
    out = []
    for s, e in ranges:
        s, e = snap(s), snap(e)
        if e - s < 1 / OF:
            continue
        gap = s - out[-1][1] if out else 99.0
        if out and (gap < 0.02 or ((e - s < 0.3 or out[-1][1] - out[-1][0] < 0.3) and gap <= 0.5)):
            out[-1][1] = max(out[-1][1], e)  # nur KLEINE Lücken überbrücken
        elif out and e - s < 0.3:
            # Mini-Stück hinter einer großen Lücke (z. B. nach einer weggerafften Kau-Pause): NICHT mit dem Abschnitt davor
            # verbinden – das würde die ganze Pause wieder einfügen (Test 01) – sondern nach vorne auf 0,3 s verlängern
            out.append([max(out[-1][1] + 0.02, snap(e - 0.3)), e])
        else:
            out.append([s, e])
    return out


def _frame(t, c):
    """Bildausschnitt (wie im fertigen Clip) zum Zeitpunkt t, klein und weichgezeichnet – für den Bildvergleich."""
    CAPV.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000)
    ok, fr = CAPV.read()
    if not ok:
        return None
    fr = fr[c["y"]:c["y"] + c["h"], c["x"]:c["x"] + c["w"]]
    g = cv2.cvtColor(cv2.resize(fr, (90, 160)), cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(g, (5, 5), 0).astype(np.float32)


SMOOTH_MAX_DIFF = 9.0  # mittlerer Bildunterschied (0–255), bis zu dem ein Schnitt überblendet wird


def transitions(ranges, crops):
    """Übergang je Schnitt (Sekunden Überblendung, 0 = harter Schnitt). Clean: sind die Bilder vor und nach dem Schnitt fast
    gleich (Person hat sich kaum bewegt), wird ganz kurz überblendet („Smooth Cut“) – der Mini-Sprung beim Rausschneiden von
    Füllwörtern/Pausen verschwindet. Hat sich viel bewegt, gäbe eine Überblendung Geisterbilder (KI-Abnahme 29.09.) →
    sauberer harter Schnitt. Zoom-Wechsel und Viral-Punches bleiben immer harte Schnitte."""
    d, diffs = [0.0], []
    for k in range(1, len(ranges)):
        a, b = crops[k - 1], crops[k]
        same = a["zoom"] == b["zoom"] and abs(a["x"] - b["x"]) < 40 and abs(a["y"] - b["y"]) < 40
        if LOUD or not same:
            d.append(0.0)
            continue
        fa, fb = _frame(ranges[k - 1][1] - 1 / OF, a), _frame(ranges[k][0], b)
        diff = float(np.abs(fa - fb).mean()) if fa is not None and fb is not None else 99.0
        diffs.append(round(diff, 1))
        if diff > SMOOTH_MAX_DIFF:
            d.append(0.0)
            continue
        dur = min(0.1, 0.4 * (ranges[k][1] - ranges[k][0]), 0.4 * (ranges[k - 1][1] - ranges[k - 1][0]))
        frames = int(dur * OF)
        d.append(frames / OF if frames >= 2 and ranges[k - 1][1] + dur < DUR - 0.05 else 0.0)
    STATS["cut_diffs"] = diffs[:40]
    return d


ZOOM_RATE = 0.005 if LOUD else 0.007  # langsame Kamerafahrt nach vorne (Clean ≈ 3,5 % in 5 s)
ZOOM_MAX = 1.05
PUNCH = 1.12  # Viral: harter Zoom-Punch auf wichtige Aussagen (Tutorial: „Zooms, um wichtige Sätze zu betonen“)


def zoom_punches(idx, ranges):
    """Viral: Zeitpunkte (im fertigen Clip) für Zoom-Punches = die stärksten Momente, die die KI auch für Emoji/Sound
    ausgesucht hat. Immer mindestens einer in den ersten 5 Sekunden (Hook). Clean: keine Punches, nur ruhige Fahrt."""
    if not LOUD:
        return []
    times = est_times(ranges, WORDS)
    kept_ws = [w for w in WORDS if w["i"] in times]
    if idx not in FX:
        FX[idx] = gemini_effects(kept_ws, times)
    pts = sorted(times[f["i"]] for f in FX[idx] if f["i"] in times)
    for w in kept_ws:  # Regel 84 (Rückmeldung 05.10.): Zoom, wenn ein Produkt genannt/gezeigt wird
        t = times[w["i"]]
        if w["i"] in ZOOM_WORDS and t > 1.0 and all(abs(t - p) >= 1.8 for p in pts):
            pts.append(t)
    pts.sort()
    # TikTok-Standard: spätestens alle ~3 s ein Zoom-Wechsel – an Satzanfängen/betonten Wörtern ergänzen
    prev_t = None
    for w in sorted(kept_ws, key=lambda w: times[w["i"]]):
        t = times[w["i"]]
        starts = prev_t is None or SENT_END.search(prev_t["t"])
        if (starts or is_emph(w["t"])) and all(abs(t - p) >= 3.0 for p in pts) and t > 2.0:
            pts.append(t)
        prev_t = w
    pts.sort()
    if not any(t < 5.0 for t in pts):
        first = next((times[w["i"]] for w in kept_ws if 1.0 <= times[w["i"]] <= 4.5 and is_emph(w["t"])), None)
        pts.insert(0, first if first is not None else min(1.5, sum(olen(s, e) for s, e in ranges) / 3))
    return pts


def punch_at(pts, t):
    """Zoom-Faktor eines Punches zur Zeit t: schnell rein (0,07 s), 1,3 s halten, weich raus (0,25 s)."""
    z = 1.0
    for p in pts:
        if p - 0.07 <= t <= p + 1.55:
            a = min(1.0, (t - p + 0.07) / 0.07)
            b = min(1.0, max(0.0, (p + 1.55 - t) / 0.25))
            z = max(z, 1 + (PUNCH - 1) * min(a, b))
    return z


def _frames(jobs, q):
    """Hintergrund-Leser: je Abschnitt die benötigte Bildregion der Quelle als Einzelbilder (im Takt des fertigen Clips)."""
    import threading

    def run_():
        for s, n, rx, ry, rw, rh in jobs:
            p = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{s:.4f}", "-i", A.src, "-frames:v", str(n), "-vf",
                                  f"fps={OF / SPEED[0]:.6f},crop={rw}:{rh}:{rx}:{ry}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                                 stdout=subprocess.PIPE)
            size, last = rw * rh * 3, None
            for _ in range(n):
                buf = p.stdout.read(size)
                if len(buf) == size:
                    last = np.frombuffer(buf, np.uint8).reshape(rh, rw, 3)
                q.put(last)  # Datei zu Ende → letztes Bild wiederholen
            p.stdout.close()
            p.wait()
        q.put(StopIteration)
    th = threading.Thread(target=run_, daemon=True)
    th.start()
    return th


LEAK_S = (0.15, 0.4)  # Lichtleck: so lange vor/nach dem Schnitt (s)


def _leak(p, seed):
    """Lichtleck wie in den Malo's-Videos des Nutzers: warmer Schimmer (orange → rosa), der einmal quer durchs Bild zieht.
    p = Fortschritt 0..1. Rückgabe: Überlagerung (1920×1080×3, 0–255) für „Screen“-Mischung."""
    h, w = 192, 108
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    env = np.sin(np.pi * min(max(p, 0.0), 1.0)) ** 1.4 * 0.75
    d = 1 if seed % 2 == 0 else -1
    img = np.zeros((h, w, 3), np.float32)
    for (b, g, r), ox, oy, rad in (((40, 140, 255), -0.35, 0.3, 0.55), ((150, 80, 255), -0.6, 0.7, 0.45)):
        cx = (ox + 1.7 * p) if d > 0 else (1 - ox - 1.7 * p)
        m = np.exp(-(((xx / w - cx) / rad) ** 2 + ((yy / h - oy) / (rad * 1.6)) ** 2))
        img += m[..., None] * np.array([b, g, r], np.float32)
    return cv2.resize(np.clip(img * env, 0, 255), (1080, 1920), interpolation=cv2.INTER_LINEAR)


def leak_cuts(ranges):
    """Viral/Premium: Lichtleck an großen Zeitsprüngen (neue Frage, neuer Abschnitt) – höchstens alle 6 s, max. 4."""
    if not LOUD:
        return []
    out, t, last = [], 0.0, -99.0
    for k, (s, e) in enumerate(ranges):
        if k and s - ranges[k - 1][1] >= 3.0 and t >= 2.0 and t - last >= 6.0 and len(out) < 4:
            out.append(t)
            last = t
        t += olen(s, e)
    return out


def render_cut(idx, ranges):
    """1. Durchgang: Abschnitte zuschneiden, Bild stabilisieren/ruhig halten/zoomen, Schnitte weich überblenden,
    Ton weich überblenden, Lautstärke angleichen (leise Stimmen hoch, laute runter) und auf Social-Media-Lautheit bringen.
    Das Bild wird je Frame subpixel-genau berechnet (Verschiebung, Drehung, Zoom in einem Schritt, bikubisch) – keine
    Pixel-Stufen, kein Mikro-Ruckeln bei Kamerafahrt, Punch oder Stabilisierung."""
    import queue
    cut = os.path.join(A.out, f"cut-{idx}.mp4")
    stab = plan_stab(ranges)
    shift, pad = (stab["mean"], stab["lim"]) if stab else (None, None)
    crops = plan_crops(ranges, 1.0, shift, pad)
    # Regel 93 (Nutzer 05.10.: „perfekt stabil, aber NICHT reinzoomen“): kein Zusatz-Zoom mehr. Der Ausschnitt darf beim
    # Ausgleichen über den Bildrand hinaus – der schmale fehlende Streifen wird gespiegelt aufgefüllt (fällt am Rand nicht auf).
    zs = 1.0
    STATS["stab_zoom"] = 1.0
    if zs > 1.0005:
        crops = plan_crops(ranges, zs, shift, pad)
    xd = transitions(ranges, crops)
    STATS["smooth_cuts"] = sum(1 for x in xd if x > 0)
    n = len(ranges)
    nf = seg_frames(ranges)
    punches = zoom_punches(idx, ranges)
    STATS["zoom_punches"] = [round(t, 1) for t in punches]
    leaks = leak_cuts(ranges)
    STATS["light_leaks"] = [round(t, 1) for t in leaks]
    # Bildplan: je Ausgabebild Fenster-Mitte (x, y), Breite, Drehung – in Quell-Pixeln
    plan, zc, off, clamped = [], 1.0, 0.0, 0
    for k, (s, e) in enumerate(ranges):
        c = crops[k]
        n_ext = int(round(xd[k + 1] * OF)) if k + 1 < n else 0  # Bilder für die Überblendung in den nächsten Abschnitt
        if xd[k] <= 0:
            zc = 1.0
        fxp = min(0.8, max(0.2, (c["c"][0] * W - c["fx"]) / c["fw"])) if c["c"] else 0.5  # Zoom ums Gesicht
        fyp = min(0.7, max(0.2, (c["c"][1] * H - c["fy"]) / c["fh"])) if c["c"] else 0.38
        fr = []
        for i in range(nf[k] + n_ext):
            tl = i / OF
            z = min(ZOOM_MAX, zc + ZOOM_RATE * tl) * punch_at(punches, off + tl)
            w, h = c["fw"] / z, c["fh"] / z
            cx = c["fx"] + (c["fw"] - w) * fxp + w / 2
            cy = c["fy"] + (c["fh"] - h) * fyp + h / 2
            a = 0.0
            if stab:
                dx, dy, a = stab["corr"][k][i] if i < nf[k] else stab["ext"](k, s + tl * SPEED[0])
                cx, cy = cx + dx - a * (cy - H / 2), cy + dy + a * (cx - W / 2)  # Drehung um die Bildmitte gemessen → Hebel
            sa, ca = abs(np.sin(a)), np.cos(a)
            ex, ey = (w * ca + h * sa) / 2, (w * sa + h * ca) / 2
            ncx, ncy = cx, cy  # nicht am Bildrand festhalten (das wäre wieder Wackeln) – der Rand wird gespiegelt
            clamped += cx < ex or cx > W - ex or cy < ey or cy > H - ey
            fr.append((ncx, ncy, w, a, ex, ey))
        zc = min(ZOOM_MAX, zc + ZOOM_RATE * olen(s, e))
        off += olen(s, e)
        plan.append(fr)
    STATS["stab_clamped"] = round(clamped / max(1, sum(len(f) for f in plan)), 3)
    # Quell-Region je Abschnitt (nur der Teil, der gebraucht wird → weniger Daten)
    jobs = []
    for k, fr in enumerate(plan):
        x0 = max(0, int(min(f[0] - f[4] for f in fr)) - 4) // 2 * 2
        y0 = max(0, int(min(f[1] - f[5] for f in fr)) - 4) // 2 * 2
        x1 = min(W, int(max(f[0] + f[4] for f in fr)) + 6)
        y1 = min(H, int(max(f[1] + f[5] for f in fr)) + 6)
        jobs.append((ranges[k][0], len(fr), x0, y0, (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2))
    total = sum(olen(s, e) for s, e in ranges)
    fc = []
    for k, (s, e) in enumerate(ranges):
        ea = min(DUR, e + XF) if k < n - 1 else e
        fc.append(f"[1:a]atrim=start={s:.4f}:end={ea:.4f},asetpts=PTS-STARTPTS[a{k}]")
    prev = "a0"
    for k in range(1, n):
        fc.append(f"[{prev}][a{k}]acrossfade=d={XF}:c1=tri:c2=tri[x{k}]")
        prev = f"x{k}"
    # Ton wie im Tutorial: Rauschen leicht reduzieren (Straße, Wind, Klimaanlage), Stimmen angleichen, Social-Lautheit,
    # weicher Ein-/Ausklang (kein Knacken am Anfang, kein abgehackter letzter Ton)
    # Stimme hervorheben (Test 30.09.: Straßen-Umfrage 08 → Whisper erkennt 59 statt 22 Wörter, KI-Hörtest „klarer“):
    # Brummen/Wind unter 80 Hz raus, dumpfe Mitten (220 Hz) etwas runter, Sprachverständlichkeit (3 kHz) etwas hoch.
    # Bewusst mild – stärkere Filter/Kompression klangen blechern und verschlechterten die Erkennung.
    # Rückmeldung 05.10.: leise Antworten („2 Euro“) gingen unter → Lautstärke-Angleich kräftiger (bis ×12, kürzere Fenster).
    tempo = f"atempo={SPEED[0]:.3f}," if SPEED[0] != 1.0 else ""  # schneller, Tonhöhe bleibt
    # Inhaber 07.10.: „Der Ton klingt wie unter Wasser – er soll clean bleiben, aber nicht gefaked.“ Ursache war die
    # Entrauschung (afftdn, nahm 7 dB Grundrauschen samt einem Teil der Stimme weg) und der kräftige Lautstärke-Angleich
    # (bis ×12, pumpte zwischen den Wörtern). Im Hörvergleich wählte er „eine Mischung aus neu und Original“: KEINE
    # Entrauschung mehr, nur Brummen unter 80 Hz raus, die Stimme ganz leicht betont, leise Stellen sanft und langsam angehoben
    # (höchstens ×2,5), dann auf Sendelautstärke.
    fc.append(f"[{prev}]{tempo}highpass=f=80:p=2,equalizer=f=220:t=q:w=1.2:g=-0.8,"
              f"equalizer=f=3000:t=q:w=1.2:g=0.8,dynaudnorm=f=500:g=31:p=0.9:m=2.5,loudnorm=I=-14:TP=-1.5:LRA=11,"
              f"afade=t=in:d=0.03,afade=t=out:st={max(0.0, total - 0.18):.3f}:d=0.18,aresample=48000[ac]")
    fc.append(f"[0:v]setsar=1,{GRADE},format=yuv420p[vc]")
    script = os.path.join(A.out, f"cut-{idx}.filter")
    open(script, "w").write(";\n".join(fc))
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "1080x1920",
                            "-framerate", f"{OF:.6f}", "-i", "-", "-i", A.src, "-/filter_complex", script, "-map", "[vc]",
                            "-map", "[ac]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-c:a", "aac", "-b:a", "256k",
                            cut], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    q = queue.Queue(maxsize=12)
    _frames(jobs, q)
    tail = []  # Überblend-Bilder des vorigen Abschnitts
    fno = 0
    try:
        for k, fr in enumerate(plan):
            rx, ry = jobs[k][2], jobs[k][3]
            new_tail = []
            for i, (cx, cy, w, a, _, _) in enumerate(fr):
                src = q.get()
                if src is StopIteration or src is None:
                    raise RuntimeError("Bild konnte nicht gelesen werden")
                sc = 1080.0 / w
                co, si = np.cos(a) * sc, np.sin(a) * sc
                # Quelle → Ausgabe: um −a drehen, skalieren, Fenster-Mitte auf Bildmitte
                ox, oy = rx - cx, ry - cy
                M = np.array([[co, si, co * ox + si * oy + 540.0], [-si, co, -si * ox + co * oy + 960.0]])
                out = cv2.warpAffine(src, M, (1080, 1920), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT_101)
                if i >= nf[k]:
                    new_tail.append(out)
                    continue
                if i < len(tail):  # weicher Übergang: vorigen Abschnitt ausblenden
                    al = (i + 1) / (len(tail) + 1)
                    out = cv2.addWeighted(tail[i], 1 - al, out, al, 0)
                tt = fno / OF
                for li, lt in enumerate(leaks):
                    if lt - LEAK_S[0] <= tt <= lt + LEAK_S[1]:  # Screen-Mischung: hellt auf, nie dunkler, keine harten Kanten
                        ov = _leak((tt - lt + LEAK_S[0]) / sum(LEAK_S), li)
                        out = (out + ov * (255 - out.astype(np.float32)) / 255).astype(np.uint8)
                enc.stdin.write(out.tobytes())
                fno += 1
            tail = new_tail
        enc.stdin.close()
    except BrokenPipeError:
        pass
    err = enc.stderr.read().decode(errors="replace").strip()
    if enc.wait() != 0:
        raise RuntimeError((err.splitlines() or ["ffmpeg Fehler"])[-1])
    if not A.keep_work:
        os.remove(script)
    return cut, round(total, 1)


def hear(path):
    """Fertigen Ton neu abhören – Wörter mit Zeiten im fertigen Clip."""
    tmp = os.path.join(A.out, "hoerprobe.wav")
    run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vn", "-ac", "1", "-ar", "16000", tmp])  # fertiger Ton ist schon ausgeglichen
    r = mlx_whisper.transcribe(tmp, **ASR, temperature=0.0,
                               initial_prompt=((BRAND + ". ") if BRAND else "") + ("Ähm, äh." if LANG == "de" else ""))
    os.remove(tmp)
    STATS["fillers_heard"] = len(re.findall(r"\b(ähm|äh|öhm|ähh)\b", r.get("text", "").lower()))
    return [{"t": x["word"].strip(), "s": float(x["start"]), "e": float(x["end"]), "p": float(x.get("probability", 1.0))}
            for sg in r["segments"] for x in sg.get("words", []) if x["word"].strip()]


OUT_TIME = {}  # Wort-Nummer → Zeitpunkt im fertigen Clip


def align(ranges, kept, heard):
    """Untertitel-Zeiten aus dem FERTIGEN Ton (exakt synchron), Text aus dem Original (richtige Schreibweise).
    Liefert zusätzlich die Wörter, die im fertigen Clip nicht mehr zu hören sind."""
    import difflib
    STATS.pop("captions_added", None)
    offs, t = [], 0.0
    for s, e in ranges:
        offs.append((s, e, t))
        t += olen(s, e)
    est = []  # geschätzte Zeit aus der Schnittliste (Rückfall)
    for w in kept:
        pos = next(((o + max(0.0, w["s"] - s) / SPEED[0], o + (min(e, w["e"]) - s) / SPEED[0]) for s, e, o in offs
                    if s - 0.05 <= w["s"] < e), None)
        est.append(pos)
    # Zahlwort = Ziffer (Fehlersuche 06.10.: „fünf/drei/zwei“ im Original, „5/3/2“ in der Hörprobe galten als „nicht gehört“
    # und lösten ohne Grund den sicheren Neuschnitt aus)
    zahl = {norm(k): v for k, v in dict(zwei="2", drei="3", vier="4", fünf="5", sechs="6", sieben="7", acht="8", neun="9", zehn="10",
                                         elf="11", zwölf="12", zwanzig="20", dreißig="30", vierzig="40", fünfzig="50", hundert="100").items()}
    stem = lambda x: zahl.get(norm(x), norm(x))[:5]
    want = [stem(w["t"].split("-")[-1] or w["t"]) for w in kept]
    got = [stem(h["t"].split("-")[-1] or h["t"]) for h in heard]
    sm = difflib.SequenceMatcher(a=want, b=got, autojunk=False)
    match = {}
    for blk in sm.get_matching_blocks():
        for d in range(blk.size):
            match[blk.a + d] = blk.b + d
    words_out, miss = [], []
    pos_end = lambda i: heard[match[i]]["e"] if i in match else (est[i][1] if est[i] else 0.0)
    for i, w in enumerate(kept):
        glue = re.fullmatch(r"[.:]\d{1,2}\.?[,!?]?", w["t"].strip())  # „11“ „.05.“ → Untertitel „11.05.“
        if glue and words_out and re.search(r"\d\.?$", words_out[-1][0]):
            tx, s0, e0 = words_out[-1]
            words_out[-1] = (tx.rstrip(".") + w["t"].strip(), s0, max(e0, pos_end(i)))
            continue
        if re.fullmatch(r"-[a-zäöüß][\wäöüß]*[.,!?…]*", w["t"].strip()) and words_out and re.search(r"[\wäöüß]$", words_out[-1][0]):
            tx, s0, e0 = words_out[-1]  # Wortteil („Frühstücks“ „-optionen“) → „Frühstücksoptionen“
            words_out[-1] = (tx + w["t"].strip()[1:], s0, max(e0, pos_end(i)))
            continue
        if not norm(w["t"]) and re.fullmatch(r"[.,!?;:…\-–—\"'»«„“]+", w["t"].strip() or "."):  # Satzzeichen → ans Wort davor
            pt = w["t"].strip()
            if words_out and pt and not re.fullmatch(r"[\-–—]+", pt):  # Striche nie anhängen („MontanaBlack.-“)
                tx, s0, e0 = words_out[-1]
                if not (re.search(r"[.,!?;:…]$", tx) and re.fullmatch(r"[.,!?;:…]+", pt)):  # kein „schön..“
                    words_out[-1] = (tx + pt, s0, e0)
            continue
        pos = None
        if i in match:
            h = heard[match[i]]
            pos = (h["s"], max(h["e"], h["s"] + 0.08))
            if est[i] and abs(pos[0] - est[i][0]) > 0.8:  # Zuordnung passt zeitlich nicht → lieber die Schnittliste
                pos = (est[i][0], max(est[i][1], est[i][0] + 0.08))
        else:
            if not re.fullmatch(r"[\W\d_]+", w["t"]) and not w.get("unclear"):  # „&“, „11.05.“ klingen anders; Gemurmel egal
                miss.append(w["t"])
            if est[i]:
                pos = (est[i][0], max(est[i][1], est[i][0] + 0.08))
        if pos:
            OUT_TIME[w["i"]] = pos[0]
        if pos and not w.get("unclear"):  # Unverständliches: Ton bleibt, aber kein (falscher) Untertitel
            words_out.append((w["t"], pos[0], pos[1]))
    # Was im fertigen Clip klar zu hören ist, aber noch keinen Untertitel hat (z. B. ein Satz, den die erste Erkennung im
    # Durcheinander überhört hat – der Ton blieb drin, der Text fehlte): aus der Hörprobe nachtragen. Nur in echten Lücken,
    # nie Füllwörter, nie einzelne Kurz-Wörter (Rauschen).
    used = set(match.values())
    groups, cur = [], []
    for j, h in enumerate(heard):
        if j in used or norm(h["t"]) in FILLER or not norm(h["t"]):
            if cur:
                groups.append(cur)
            cur = []
            continue
        if cur and h["s"] - cur[-1]["e"] > 0.8:
            groups.append(cur)
            cur = []
        cur.append(h)
    if cur:
        groups.append(cur)
    added = []
    for g in groups:
        if (len(g) < 2 and len(norm(g[0]["t"])) < 4) or sum(h["p"] for h in g) / len(g) < 0.6:
            continue  # zu kurz oder unsicher gehört → lieber kein Untertitel als ein falscher
        a, b = g[0]["s"], g[-1]["e"]
        if any(x[1] < b + 0.15 and x[2] > a - 0.15 for x in words_out):
            continue  # dort steht schon ein Untertitel
        at = next((k for k, x in enumerate(words_out) if x[1] > a), len(words_out))  # vorhandene Reihenfolge bleibt unangetastet
        words_out[at:at] = [(h["t"], h["s"], max(h["e"], h["s"] + 0.08)) for h in g]
        added += [h["t"] for h in g]
    if added:
        STATS["captions_added"] = added[:30]
    for k in range(1, len(words_out)):  # Reihenfolge = Sprechreihenfolge; Zeiten nur nach vorne schieben, nie umsortieren
        if words_out[k][1] < words_out[k - 1][1] + 0.04:
            tx, s0, e0 = words_out[k]
            words_out[k] = (tx, words_out[k - 1][1] + 0.04, max(e0, words_out[k - 1][1] + 0.12))
    return words_out, miss


SFX_DIR = os.environ.get("VELOX_SFX", os.path.join(MASCHINE, "sfx"))
EMOJI_DIR = os.environ.get("VELOX_EMOJI", os.path.join(MASCHINE, "emoji"))
EMOJIS = {"😋": "1f60b", "🔥": "1f525", "😍": "1f60d", "🤤": "1f924", "😂": "1f602", "🤯": "1f92f", "😮": "1f62e", "🤔": "1f914",
          "👀": "1f440", "💸": "1f4b8", "💰": "1f4b0", "🎉": "1f389", "🏆": "1f3c6", "💯": "1f4af", "👍": "1f44d", "👏": "1f44f",
          "❤️": "2764", "✅": "2705", "❌": "274c", "⭐": "2b50", "🍰": "1f370", "🍔": "1f354", "🍕": "1f355", "☕": "2615",
          "🍫": "1f36b", "🍌": "1f34c", "🍓": "1f353", "🎁": "1f381", "📍": "1f4cd", "⏰": "23f0", "🚀": "1f680", "😎": "1f60e"}
SFX_NAMES = ("pop", "ding", "kaching", "whoosh", "click")
RISER_S = 1.2  # Länge des Riser-Sounds (make_sfx.py)
FX = {}  # Effekte je Clip (bleiben beim Neu-Einbrennen nach der Abnahme gleich)


MUSIC_DIR = os.environ.get("VELOX_MUSIK", os.path.join(MASCHINE, "musik"))
MUSIC = {  # lizenzfrei (Pixabay, OHNE Content ID – siehe musik/LIZENZ.txt)
    "hiphop-upbeat": "Hip-Hop, gut gelaunt, treibend – Street-Food, Umfragen, Challenges",
    "hiphop-upbeat2": "Hip-Hop, locker und positiv – Vorstellung, Vlog",
    "hiphop-beat": "klassischer Hip-Hop-Beat, cool – Restaurant, Produkt, Lifestyle",
    "hiphop-groove": "Hip-Hop-Groove, edel und entspannt – Premium, Food-Details",
    "lofi": "Lo-Fi, ruhig und gemütlich – Erklärungen, Monolog, Café, ruhige Stimmung",
    "funky": "Funk, verspielt und fröhlich – Spaß, Quiz, lustige Reaktionen",
    "funk": "Funk, groovig mit Energie – Wettessen, Action, Herausforderung",
    "happy": "fröhlicher Pop, hell und freundlich – Familie, Kinder, gute Laune",
    "joyful": "fröhlich-beschwingt, kurz – sehr kurze Clips unter 40 s",
    "trap": "Trap, hart und modern – Hype, Wettkampf, junge Zielgruppe",
}
MUSIC_PICK = {}


def pick_music(kept):
    """Passende Hintergrundmusik: bei --music auto wählt die KI nach Inhalt und Stimmung, sonst der genannte Titel."""
    if A.music == "none" and not (PLAN or {}).get("zeigen"):  # stumme Zeige-Strecken brauchen Musik (Regel 114)
        return None
    have = {n: d for n, d in MUSIC.items() if os.path.exists(os.path.join(MUSIC_DIR, f"{n}.mp3"))}
    if A.music in have:
        return os.path.join(MUSIC_DIR, f"{A.music}.mp3")
    if not have:
        return None
    res = gemini("Du bist Editor für Kurzvideos. Wähle die Hintergrundmusik, die am besten zu Inhalt und Stimmung passt. "
                 "Stil des Schnitts: " + {"clean": "ruhig, sauber", "viral": "schnell, laut", "premium": "hochwertig, kinoreif"}[A.style] + ".",
                 "Musik:\n" + "\n".join(f"{n}: {d}" for n, d in have.items()) + "\n\nTranskript: " + " ".join(w["t"] for w in kept)[:3000],
                 {"type": "object", "properties": {"musik": {"type": "string", "enum": list(have)}}, "required": ["musik"]})
    name = (res or {}).get("musik")
    if name not in have:
        name = {"clean": "lofi", "viral": "hiphop-upbeat", "premium": "hiphop-groove"}[A.style]
        name = name if name in have else next(iter(have))
    STATS["music"] = name
    return os.path.join(MUSIC_DIR, f"{name}.mp3")


TRENDS = os.path.join(MASCHINE, "trends.json")


def post_text(kept, title):
    """Fertiger Beschreibungstext zum Posten (TikTok/Instagram/YouTube Shorts) – spart dem Kunden die Arbeit nach dem
    Download: 1–2 kurze Sätze + Aufruf, 5–8 Hashtags (Nische, Ort falls genannt, ein, zwei breite)."""
    return post_text_for(title, " ".join(w["t"] for w in kept)[:2500])


def post_text_for(title, inhalt):
    res = gemini("Du bist Social-Media-Manager für Unternehmen. Schreibe den Beschreibungstext für diesen Kurzvideo-Post "
                 "(TikTok/Instagram Reels/YouTube Shorts) auf Deutsch: 1–2 kurze, natürliche Sätze, die neugierig machen oder "
                 "den Nutzen nennen, dann ein kurzer Aufruf (Kommentieren, Vorbeikommen, Folgen – passend zum Inhalt). Höchstens "
                 "2 Emojis, keine Übertreibungen, nichts erfinden (keine Preise/Orte, die nicht vorkommen). Dazu 5–8 Hashtags ohne "
                 "Leerzeichen: 3–4 zur Nische/zum Produkt, der Ort, falls er genannt wird, 1–2 breite (z. B. #foodtok)."
                 + (f" Schreibweise von Namen/Marken: {A.words}." if A.words else ""),
                 f"Titel: {title}\nGesagt wird: {inhalt}",
                 {"type": "object", "properties": {"text": {"type": "string"}, "hashtags": {"type": "array", "items": {"type": "string"}}},
                  "required": ["text", "hashtags"]})
    if not res or not (res.get("text") or "").strip():
        return None
    tags = []
    for h in res.get("hashtags") or []:
        h = "#" + re.sub(r"[^\wäöüÄÖÜß]", "", str(h))
        if len(h) > 2 and h.lower() not in (t.lower() for t in tags):
            tags.append(h)
    return {"text": res["text"].strip()[:600], "hashtags": tags[:8]}


def suggest_songs(kept, title):
    """2–3 Trend-Songs als VORSCHLAG (nicht eingebaut): der Kunde fügt sie beim Posten in der TikTok-App hinzu. Quelle:
    Shazam-Charts DE (trends.py, wöchentlich). Unpassendes (derb, düster, falsche Stimmung für eine Marke) fliegt raus."""
    try:
        tr = json.load(open(TRENDS))
    except Exception:  # noqa: BLE001
        return []
    songs = tr.get("songs") or []
    if not songs:
        return []
    res = gemini("Du bist Social-Media-Manager. Schlage für dieses Kurzvideo 3 Trend-Songs vor, die der Kunde beim Posten auf "
                 "TikTok als Sound hinzufügen kann. Nur Songs, die zu Inhalt, Stimmung und einer seriösen Marke passen (nichts "
                 "Derbes, Anzügliches, Düsteres, Aggressives – außer das Video ist genau so). Bevorzuge Aufsteiger (discovery). "
                 "Je Song eine kurze Begründung auf Deutsch (max. 10 Wörter).",
                 f"Titel: {title}\nTranskript: {' '.join(w['t'] for w in kept)[:2500]}\n\nTrend-Songs:\n" +
                 "\n".join(f"{i}. {x['song']} – {x['artist']} [{x.get('chart')}] {x.get('mood', '')}" for i, x in enumerate(songs)),
                 {"type": "object", "properties": {"songs": {"type": "array", "items": {"type": "object", "properties": {
                     "nr": {"type": "integer"}, "warum": {"type": "string"}}, "required": ["nr", "warum"]}}}, "required": ["songs"]})
    out = []
    for x in (res or {}).get("songs") or []:
        n = x.get("nr")
        if isinstance(n, int) and 0 <= n < len(songs) and all(o["song"] != songs[n]["song"] for o in out):
            out.append({"song": songs[n]["song"], "artist": songs[n]["artist"], "warum": str(x.get("warum") or "")[:80]})
    STATS["songs"] = out[:3]
    return out[:3]


def gemini_hook(kept):
    """Titel für die ersten 3 Sekunden – ehrlich, aus dem Inhalt, kein Satzanfang.
    Viral: packend, darf ein Emoji haben. Clean: ruhig und hochwertig, ohne Emoji, kein Clickbait."""
    if not LOUD:
        res = gemini(
            "Schreibe EINEN kurzen Titel für die ersten 3 Sekunden eines hochwertigen, ruhigen Kurzvideos (TikTok/Reels). "
            "2–6 Wörter, Sprache des Videos, sachlich-elegant, fasst zusammen, worum es geht (Ort, Produkt, Person, Thema), "
            "macht dezent neugierig, ehrlich – nichts erfinden, kein Clickbait, kein Emoji, keine Hashtags, kein abgeschnittener "
            "Satzanfang. Beispiele: „Kaiserschmarrn in Mannheim“, „Von Afghanistan nach Köln“, „Pizza & Paint am Muttertag“.",
            " ".join(w["t"] for w in kept),
            {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]})
        t = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]", "", ((res or {}).get("title") or "")).strip().strip('"„“')
        return t if 0 < len(t) <= 40 else ""
    res = gemini(
        "Schreibe EINEN Hook-Titel für die ersten 3 Sekunden eines viralen Kurzvideos (TikTok/Reels). 2–5 Wörter, Sprache des "
        "Videos, macht neugierig, fasst den Kern zusammen (Produkt, Aktion, Frage, Pointe), ehrlich – nichts erfinden, "
        "kein abgeschnittener Satzanfang, keine Hashtags, höchstens ein Emoji am Ende. Beispiele: „Pizza & Paint am Muttertag 🎨“, "
        "„Würdest du das probieren?“, „10 von 10 für diesen Schmarrn“.",
        " ".join(w["t"] for w in kept),
        {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]})
    t = ((res or {}).get("title") or "").strip().strip('"„“')
    return t if 0 < len(t) <= 40 else ""


def est_times(ranges, words):
    """Zeitpunkt jedes Wortes im fertigen Clip – schon VOR dem Rendern (aus der Schnittliste). Wort-Nummer → Sekunde."""
    out, off = {}, 0.0
    for s, e in ranges:
        for w in words:
            if s - 0.05 <= w["s"] < e:
                out.setdefault(w["i"], off + max(0.0, w["s"] - s) / SPEED[0])
        off += olen(s, e)
    return out


ZOOM_WORDS = set()  # Wortnummern mit Produkt/Gericht → kurzer Zoom-Punch (gemini_effects füllt, zoom_punches nutzt)


def gemini_effects(kept, times):
    """Viral: KI setzt wenige, passende Akzente – Emoji + Sound + Zoom-Punch an Pointen (Preis → Kaching, Bewertung → Ding …).
    Läuft VOR dem Rendern (Zeiten aus der Schnittliste), damit die Zoom-Punches genau auf diese Momente fallen."""
    OUT_TIME = times  # noqa: N806  (gleiche Zeitbasis wie später im fertigen Clip)
    res = gemini(
        "Du bist Editor für virale Kurzvideos. Setze SPARSAM Akzente an die stärksten Momente des Transkripts (Pointen, Zahlen, "
        "Preise, Bewertungen, überraschende Reaktionen, das Produkt). Höchstens ein Akzent pro 6 Sekunden, insgesamt 2–6. "
        "Je Akzent: Wortnummer (das Wort, bei dem er erscheint), „text“ = das Schlüsselwort bzw. die Zahl genau wie gesagt "
        "(1–3 Wörter, z. B. „Kaiserschmarrn“, „10 von 10“, „150 Millionen“, „12 Euro“), ein passendes Emoji aus dieser Liste: "
        + " ".join(EMOJIS) +
        " und ein Sound aus: pop (Standard), ding (Bewertung/Pointe), kaching (Geld/Preis/Gewinn), whoosh (Überraschung), click "
        "(Aufzählung). Außerdem „zoom“: Wortnummern, bei denen ein Produkt, Gericht oder eine Sorte genannt bzw. gezeigt wird "
        "(z. B. „Classic“, „Topping nach Wahl“, „Smashburger“) – dort zoomt das Bild kurz rein (ohne Emoji/Sound), höchstens "
        "einer pro 3 Sekunden. "
        + ("PREMIUM: Hier bekommt JEDER genannte Preis/Betrag und JEDES aufgezählte Produkt, Angebot oder Stichwort einen eigenen "
           "Akzent – genau bei dem Wort, bei dem es gesagt wird („für unter 10 Euro“ → text „10 €“, sfx kaching; „drei Slices“ → "
           "text „3 Slices“; „Eislatte“ → text „Eislatte“). Bis zu 10 Akzente, mindestens 1,5 Sekunden Abstand; text in normaler "
           "Schreibweise (nicht in Großbuchstaben), bei Preisen immer mit „€“. " if PREM else "")
        + "Wörter als [Nummer|Sekunde]Wort.",
        " ".join(f"[{w['i']}|{OUT_TIME[w['i']]:.1f}s]{w['t']}" for w in kept if w["i"] in OUT_TIME),
        {"type": "object", "properties": {"fx": {"type": "array", "items": {"type": "object", "properties": {
            "i": {"type": "integer"}, "text": {"type": "string"}, "emoji": {"type": "string"}, "sfx": {"type": "string"}},
            "required": ["i", "text", "emoji", "sfx"]}},
            "zoom": {"type": "array", "items": {"type": "integer"}}},
         "required": ["fx"]})
    ZOOM_WORDS.update(int(i) for i in (res or {}).get("zoom") or [] if isinstance(i, int))  # Regel 84: Zoom aufs Produkt
    fx, last = [], -99.0
    for f in sorted((res or {}).get("fx") or [], key=lambda f: OUT_TIME.get(f.get("i"), 1e9)):
        t0 = OUT_TIME.get(f.get("i"))
        if t0 is None or t0 < 0.8 or t0 - last < (1.5 if PREM else 4.0):
            continue
        fx.append({"i": f["i"], "t": t0, "text": " ".join(str(f.get("text") or "").split()[:4])[:40], "emoji": f["emoji"] if f["emoji"] in EMOJIS else None,
                   "sfx": f["sfx"] if f["sfx"] in SFX_NAMES else "pop"})
        last = t0
    return fx[:10 if PREM else 6]


def viral_sounds(ranges, fx):
    """Sound-Liste (Zeit, Datei, Lautstärke): Whoosh zum Hook-Titel und an Zoom-Punches, dazu die KI-Akzente."""
    snd, t, last = [(0.0, "whoosh", 0.28)], 0.0, 0.0
    for k, (s, e) in enumerate(ranges):
        if k and k % 2 == 1 and t - last >= 3.5 and not any(abs(f["t"] - t) < 1.2 for f in fx):
            snd.append((max(0.0, t - 0.12), "whoosh", 0.22))
            last = t
        t += olen(s, e)
    snd += [(f["t"], f["sfx"], 0.32 if f["sfx"] in ("pop", "click") else 0.26) for f in fx]
    if PREM:  # jede Motion-Graphic bekommt ihren Whoosh (Tutorial/TikToks: „Sound zu jeder Bewegung“)
        snd += [(max(0.0, f["t"] - 0.12), "whoosh", 0.2) for f in fx if f["t"] >= 3.2]
    big = [f for f in fx if f["sfx"] in ("ding", "kaching") and f["t"] >= 4.0]
    if big:  # Spannung vor der Auflösung: leiser Riser, der genau in den letzten großen Moment läuft (Tutorial: „reversed hit“)
        snd.append((max(0.0, big[-1]["t"] - RISER_S), "riser", 0.16))
    return [(t0, n, v) for t0, n, v in snd if os.path.exists(os.path.join(SFX_DIR, f"{n}.wav"))]


EMOJI_PX = 140   # Emoji-Größe im fertigen Bild (Quelle 160 px → wird nur verkleinert, bleibt scharf)
HOOK_Y = SAFE_T  # Oberkante der Titel-Karte (unter der Statusleiste/TikTok-Suche)
LOGO_PX, LOGO_Y = 150, SAFE_T  # Kunden-Logo oben links (Breite, Oberkante) – unter den Leisten der Apps (Regel 112)


def logo_png(src, out):
    """Inhaber 07.10.: „Das Logo sieht hingeklebt aus.“ Das Logo wird als kleines, sauberes Abzeichen eingesetzt: kleiner
    (116 statt 190 px), abgerundete Ecken, weicher Schatten, leicht durchscheinend – wie ein Wasserzeichen eines Senders,
    nicht wie ein aufgeklebtes Bild. Hat das Logo schon einen durchsichtigen Hintergrund, bleibt seine Form erhalten."""
    try:
        from PIL import Image, ImageDraw, ImageFilter
        im = Image.open(src).convert("RGBA")
        w = 4 * LOGO_PX
        im = im.resize((w, max(8, round(im.height * w / im.width))), Image.LANCZOS)
        if im.height > w:  # sehr hohes Logo → auf Quadrat begrenzen
            im = im.crop((0, (im.height - w) // 2, w, (im.height + w) // 2))
        a = im.getchannel("A")
        if a.getextrema()[0] > 250:  # kein durchsichtiger Hintergrund → abgerundetes Abzeichen
            m = Image.new("L", im.size, 0)
            ImageDraw.Draw(m).rounded_rectangle((0, 0, im.width - 1, im.height - 1), radius=int(min(im.size) * 0.24), fill=255)
            im.putalpha(m)
            a = m
        pad = 4 * 14
        canvas = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad), (0, 0, 0, 0))
        sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        sh.paste((0, 0, 0, 110), (pad, pad + 10), a)
        canvas = Image.alpha_composite(canvas, sh.filter(ImageFilter.GaussianBlur(22)))
        im.putalpha(a.point(lambda v: int(v * 0.93)))
        canvas.alpha_composite(im, (pad, pad))
        canvas = canvas.resize((canvas.width // 4, canvas.height // 4), Image.LANCZOS)
        canvas.save(out)
        return out, 14
    except Exception:  # noqa: BLE001
        return src, 0
APPLE_EMOJI = "/System/Library/Fonts/Apple Color Emoji.ttc"


def hook_card(text, path, flach=False):
    """Titel oben als saubere Karte mit runden Ecken, max. 2 ausgewogene Zeilen.
    Viral: weiß, weicher Schatten, größer, Emoji am Ende als echtes Apple-Emoji.
    Clean: dezent – dunkles, leicht durchsichtiges Glas, weiße Schrift, kleiner, ohne Emoji."""
    calm = not LOUD
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
    except ImportError:
        return None
    emo = None if calm else next((ch for ch in EMOJIS if text.rstrip().endswith(ch)), None) or \
        (re.search(r"([\U0001F300-\U0001FAFF\u2600-\u27BF][\uFE0F]?)\s*$", text) or [None])[0]
    words = re.sub(r"[^\w\s&?!.,:'’\-€%/+]", "", text).split()
    if not words:
        return None
    font = ImageFont.truetype(os.path.join(FONTS, "Montserrat-Bold.otf"), 54 if calm else 64)
    maxw = 700 if calm else 730  # Karte samt Rand ≤ 820 px → bleibt im sichtbaren Bereich von Instagram (Regel 112)
    one = " ".join(words)
    if flach:  # Regel 112: einzeilige, flachere Karte für den schmalen Streifen über dem Kopf – Schrift so weit nötig kleiner
        for px in range(54 if calm else 64, 39, -2):
            font = ImageFont.truetype(os.path.join(FONTS, "Montserrat-Bold.otf"), px)
            if font.getlength(one) + (82 if emo else 0) <= maxw:
                break
        else:
            return None
    if font.getlength(one) + (82 if emo else 0) <= maxw:
        lines = [one]
    else:  # zwei ausgewogene Zeilen („Pizza & Paint / am Muttertag“ statt „Pizza & Paint am / Muttertag“)
        best = min(range(1, len(words)), key=lambda k: max(font.getlength(" ".join(words[:k])),
                                                              font.getlength(" ".join(words[k:])) + (82 if emo else 0)))
        lines = [" ".join(words[:best]), " ".join(words[best:])]
    lh = 68 if calm else 80
    em_img = None
    if emo:
        try:
            ef = ImageFont.truetype(APPLE_EMOJI, 160)
            # Regel 102 (Nutzer 06.10.: „Emojis sind oben abgeschnitten“): Die Schrift setzt das Bild ~18 px über die Mitte –
            # auf 176 px wurde es oben gekappt. Groß rendern, auf die sichtbare Grafik zuschneiden, mittig mit Rand einsetzen.
            big = Image.new("RGBA", (320, 320), (0, 0, 0, 0))
            ImageDraw.Draw(big).text((160, 160), emo, font=ef, embedded_color=True, anchor="mm")
            big = big.crop(big.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox())
            big.thumbnail((160, 160), Image.LANCZOS)
            em_img = Image.new("RGBA", (176, 176), (0, 0, 0, 0))
            em_img.paste(big, ((176 - big.width) // 2, (176 - big.height) // 2), big)
            em_img = em_img.resize((70, 70), Image.LANCZOS)
        except Exception:  # noqa: BLE001
            em_img = None
    widths = [font.getlength(l) for l in lines]
    if em_img:
        widths[-1] += 82
    pad_x, pad_y, sh = (38, 20, 30) if calm else (44, 26, 30)
    radius = 28 if calm else 34
    cw, ch = int(max(widths)) + 2 * pad_x, lh * len(lines) + 2 * pad_y
    img = Image.new("RGBA", (cw + 2 * sh, ch + 2 * sh), (0, 0, 0, 0))
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((sh, sh + 8, sh + cw, sh + ch + 8), radius=radius, fill=(0, 0, 0, 40 if calm else 90))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(14)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((sh, sh, sh + cw, sh + ch), radius=radius, fill=(14, 14, 18, 175) if calm else (255, 255, 255, 250))
    for i, l in enumerate(lines):
        lw = widths[i]
        x0 = sh + (cw - lw) / 2
        y0 = sh + pad_y + i * lh + lh / 2
        d.text((x0, y0), l, font=font, fill=(255, 255, 255, 255) if calm else (17, 17, 20, 255), anchor="lm")
        if em_img and i == len(lines) - 1:
            img.alpha_composite(em_img, (int(x0 + font.getlength(l) + 12), int(y0 - 37)))
    img.save(path)
    return path


# ─────────────────────────────── Premium: 3D-Titel, Motion Graphics, Zähler ───────────────────────────────
def _dark(hex_, f=0.32):
    h = hex_.lstrip("#")
    r, g, b = (int(int(h[i:i + 2], 16) * f) for i in (0, 2, 4))
    return f"&H00{b:02X}{g:02X}{r:02X}"


ACC_DARK = _dark(A.color, 0.24)
POP_ANIM = "\\fscx55\\fscy55\\t(0,110,\\fscx106\\fscy106)\\t(110,190,\\fscx100\\fscy100)"


def text3d(txt, x, y, t0, t1, fs, color=WHITE, style="Pop", anim=True, depth=10, fade=(90, 160)):
    """Text mit echter Tiefe (3D-Extrusion in dunkler Akzentfarbe) und weichem Schatten – als ASS-Ebenen übereinander."""
    txt = txt.replace("{", "(").replace("}", ")")
    base = f"\\an5\\fs{fs}\\fad({fade[0]},{fade[1]})" + (POP_ANIM if anim else "")
    t = f"{ass_time(t0)},{ass_time(t1)},{style},,0,0,0,"
    ev = [f"Dialogue: 2,{t},{{{base}\\pos({x},{y + int(fs * 0.16)})\\1c&H000000&\\1a&H70&\\blur{max(8, fs // 9)}}}{txt}"]
    step = max(1.2, fs / 48)  # deutlich sichtbare Tiefe (≈ 3 px je Ebene bei großer Schrift)
    for k in range(depth, 0, -1):
        ev.append(f"Dialogue: 3,{t},{{{base}\\pos({x + k * step * 0.6:.1f},{y + k * step:.1f})\\1c{ACC_DARK}}}{txt}")
    ev.append(f"Dialogue: 4,{t},{{{base}\\pos({x},{y})\\1c{color}\\blur0.6}}{txt}")
    return ev


_POPFONT = []


def fit_fs(txt, width=900, fs_max=150, k=None):
    """Größte Schriftgröße, bei der txt in width Pixel passt – gemessen mit der echten Schrift (libass setzt Montserrat
    Black bei gleicher Größe ≈ 0,665 × so breit wie Pillow)."""
    try:
        if not _POPFONT:
            from PIL import ImageFont
            _POPFONT.append(ImageFont.truetype(os.path.join(FONTS, "Montserrat-normal-900.ttf"), 100))
        w100 = _POPFONT[0].getlength(txt) * 0.665
    except Exception:  # noqa: BLE001
        w100 = 44 * len(txt)
    return max(60, min(fs_max, int(width * 100 / max(1.0, w100))))


HIDE = []  # Zeitfenster, in denen eine Premium-Grafik steht (dort keine normalen Untertitel)
CARDS = []       # Premium: Stichwort-Karten dieses Clips (Zeit, Text, Symbol, Platz) – premium_events plant, burn zeichnet
CARD_PITCH = 146  # Höhe einer Karte samt Abstand
GRUEN = "&H0064E15A"  # Zähler am Ziel


def card_png(text, emoji, out):
    """Premium-Karte: helle, abgerundete Karte mit weichem Schatten, Symbolfeld in der Akzentfarbe, fetter Schrift.
    Rückgabe (Datei, Rand) – der Rand ist der durchsichtige Schatten-Saum rundum."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    sc, h, p = 2, 130, 40
    font = ImageFont.truetype(os.path.join(FONTS, "Montserrat-Bold.otf"), 54 * sc)
    w = int(min(660, max(340, font.getlength(text) / sc + h + 54)))
    while font.getlength(text) / sc + h + 54 > 660 and font.size > 30 * sc:   # langer Text → Schrift etwas kleiner
        font = ImageFont.truetype(os.path.join(FONTS, "Montserrat-Bold.otf"), font.size - 2 * sc)
    W, H, P = w * sc, h * sc, p * sc
    im = Image.new("RGBA", (W + 2 * P, H + 2 * P), (0, 0, 0, 0))
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((P, P + 14 * sc, P + W, P + H + 14 * sc), radius=30 * sc, fill=(0, 0, 0, 125))
    im = Image.alpha_composite(im, sh.filter(ImageFilter.GaussianBlur(18 * sc)))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((P, P, P + W, P + H), radius=36 * sc, fill=(250, 250, 252, 240), outline=(255, 255, 255, 255), width=2 * sc)
    acc = tuple(int(A.color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    d.rounded_rectangle((P + 16 * sc, P + 16 * sc, P + (h - 16) * sc, P + (h - 16) * sc), radius=24 * sc, fill=acc + (255,))
    ico = os.path.join(EMOJI_DIR, f"{EMOJIS.get(emoji, '')}.png")
    if emoji and os.path.exists(ico):
        e = Image.open(ico).convert("RGBA")
        e.thumbnail((72 * sc, 72 * sc), Image.LANCZOS)
        im.alpha_composite(e, (P + (h * sc - e.width) // 2, P + (h * sc - e.height) // 2))
    else:
        d.ellipse((P + 40 * sc, P + 40 * sc, P + (h - 40) * sc, P + (h - 40) * sc), fill=(18, 18, 22, 255))
    d.text((P + (h + 10) * sc, P + H // 2), text, font=font, fill=(18, 18, 22, 255), anchor="lm")
    im.resize((im.width // sc, im.height // sc), Image.LANCZOS).save(out)
    return out, p
NUM = re.compile(r"(\d{1,3}(?:[.\s]\d{3})+|\d+(?:,\d+)?)")


def premium_events(fx, words_out, title, voll=True):
    """Motion Graphics für Premium:
    – 3D-Titel oben (0–3 s), zwei ausgewogene Zeilen, poppt auf;
    – an den stärksten Momenten ein großes Schlüsselwort mit 3D-Tiefe („Kinetic Typography“, 1,1 s), darüber klein die
      Wörter davor (z. B. „für diesen“ / SCHMARRN);
    – Zahlen (Preise, Bewertungen, Mengen) zählen hoch („Zähler“, 0,75 s) und bleiben kurz stehen."""
    ev, karten = [], []
    HIDE.clear()
    # voll=False (Viral): nur die Preis-/Zahlen-Zähler. Abgleich mit der Website 07.10.: Das Viral-Beispiel dort zeigt einen
    # grünen Preis, der hochzählt – die Software hatte das bisher nur in Premium. Was die Website zeigt, liefert der Stil auch.
    clean_title = re.sub(r"[^\w\s&?!.,:'’\-€%/+]", "", title or "").strip().upper() if voll else ""  # „/“ bleibt: „10/10“
    lines, fs_t = [], 0
    if clean_title:
        ws = clean_title.split()
        lines = [clean_title]
        if len(clean_title) > 16 and len(ws) > 1:
            best = min(range(1, len(ws)), key=lambda k: max(len(" ".join(ws[:k])), len(" ".join(ws[k:]))))
            lines = [" ".join(ws[:best]), " ".join(ws[best:])]
        fs_t = min(fit_fs(max(lines, key=len), 680, 140), 140)
    if voll:
        plan_layout((820, int(len(lines) * fs_t * 1.08) + 30) if lines else None, False)
    if lines and not LAY["title"]:  # der Titel entfällt nie (Inhaber 06.10.) – notfalls einzeilig ganz oben am Rand
        lines = [clean_title]
        fs_t = min(fit_fs(clean_title, 640, 110), 110)   # mit Buchstabenabstand bleibt die Zeile so im sichtbaren Rahmen
        STATS.pop("titel_weggelassen", None)
        plan_layout((820, int(fs_t * 1.08) + 30), False, force=True)
    cy = caption_y()
    py = cy - 390 if cy > 900 else cy + 390
    if FACES_OUT:  # Grafik nie über ein Gesicht: unter das Kinn, sonst über den Kopf, sonst Standard
        ft, fb = face_box()[:2]
        if fb + 200 < cy - 130:
            py = int(min(cy - 200, fb + 150))
        elif ft - 200 > 560:
            py = int(ft - 150)
    if lines and LAY["title"]:
        for li, line in enumerate(lines):
            ev += text3d(line, 540, LAY["hy"] + 15 + int(fs_t * 0.54) + li * int(fs_t * 1.08), 0.05 + li * 0.08, 3.0, fs_t,
                         color=WHITE if li == 0 else ACC)
    for f in fx:
        t0, txt = f["t"], (f.get("text") or "").strip().upper()
        if not txt or t0 < 3.2:  # nicht mit dem Titel überlappen
            continue
        m = NUM.search(txt)
        if m:  # Zähler
            raw = m.group(1)
            val = float(raw.replace(".", "").replace(" ", "").replace(",", "."))
            dec = "," in raw
            start = val - 15 if 1900 <= val <= 2100 and not dec else 0.0
            pre, post = txt[:m.start()].strip(), txt[m.end():].strip()
            fs = fit_fs(f"{pre} {raw} {post}".strip(), 740, 230)
            n = 22
            for k in range(n + 1):
                p = k / n
                v = start + (val - start) * (1 - (1 - p) ** 3)
                sv = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if dec else f"{int(round(v)):,}".replace(",", ".")
                if 1900 <= val <= 2100 and not dec:
                    sv = str(int(round(v)))
                a, b = t0 + k * 0.034, (t0 + (k + 1) * 0.034) if k < n else t0 + 1.5
                if k < n:
                    ev += text3d(f"{pre} {sv} {post}".strip(), 540, py, a, a + 0.034, fs, color=WHITE, anim=(k == 0), fade=(60 if k == 0 else 0, 0))
                else:  # Inhaber 07.10.: am Ziel wird die Zahl GRÜN und poppt (dazu Kaching + Zoom) und bleibt kurz stehen
                    ev += text3d(f"{pre} {sv} {post}".strip(), 540, py, a, t0 + 2.0, fs, color=GRUEN, anim=True, fade=(0, 200))
            HIDE.append((t0 - 0.05, t0 + 2.0))
            f["sfx"] = "kaching" if re.search(r"€|euro|eur\b", txt, re.I) else f.get("sfx")
        elif voll:  # Stichwort/Produkt → Karte mit Symbol, erscheint beim gesprochenen Wort (Stil-Muster vom Inhaber bestätigt 07.10.)
            karten.append({"t": t0, "text": " ".join((f.get("text") or "").split())[:26], "emoji": f.get("emoji")})
    # Karten, die kurz nacheinander fallen (Aufzählung), stapeln sich und bleiben gemeinsam stehen
    CARDS.clear()
    gruppen = []
    for k in karten:
        if gruppen and k["t"] - gruppen[-1][-1]["t"] <= 3.2 and len(gruppen[-1]) < 4:
            gruppen[-1].append(k)
        else:
            gruppen.append([k])
    half = CAPFS[0] * 0.6
    for g in gruppen:
        ta, tb = g[0]["t"], g[-1]["t"] + 2.2
        h = len(g) * CARD_PITCH
        frei = lambda y0: SAFE_T <= y0 and y0 + h <= SAFE_B and not hits_face(210, y0, 870, y0 + h, ta, tb) \
            and not (LAY.get("trect") and ta < 3.4 and _cut(y0, y0 + h, LAY["trect"][1] - 10, LAY["trect"][3] + 10))
        oben, unten = cy - half - 26 - h, cy + half + 26
        if frei(unten) and not any(a_ < tb and b_ > ta for a_, b_ in HIDE):
            y0 = unten                                   # unter den Untertiteln – beides bleibt lesbar
        elif frei(oben):
            y0 = oben
        else:                                            # kein eigener Platz → an die Stelle der Untertitel (die pausieren so lange)
            y0 = min(max(cy - h / 2, SAFE_T), SAFE_B - h)
            if hits_face(210, y0, 870, y0 + h, ta, tb):
                g, h = g[:1], CARD_PITCH                 # zu wenig Platz für den Stapel → nur die erste Karte
                y0 = cy - h / 2
                tb = g[0]["t"] + 2.2
            HIDE.append((ta - 0.05, tb))
        for r, k in enumerate(g):
            CARDS.append({**k, "y": int(y0 + r * CARD_PITCH), "bis": tb})
    return ev


# ─────────────────────────────── 6c. Quiz-Zähler & Lichtleck (aus den Malo's-Videos des Nutzers) ───────────────────────────────
QUIZ = {}  # je Clip: {"n": Fragen gesamt, "hits": [Zeit der Auflösung einer RICHTIGEN Antwort …], "start": Zeit}


def gemini_quiz(kept, times):
    """Straßen-Quiz erkennen (wie Malo's Umfrage): Fragen, Auflösung, richtig/falsch. Nur wenn es wirklich ein Quiz mit
    mehreren Fragen ist – sonst None."""
    res = gemini(
        "Du bist Editor. Prüfe, ob dieses Transkript ein Quiz/Straßen-Quiz ist (Moderator stellt mehrere Wissensfragen, "
        "Passanten antworten, es wird aufgelöst, ob richtig). Wenn ja: Gesamtzahl der Fragen (wie angekündigt, z. B. „fünf "
        "Fragen“ → 5, sonst gezählt) und je Frage die Wortnummer, an der die Frage beginnt, und die Wortnummer, an der "
        "aufgelöst wird (z. B. „Richtig!“, „Korrekt“, „Nein, leider …“), plus ob die Antwort richtig war. "
        "Wörter als [Nummer|Sekunde]Wort.",
        " ".join(f"[{w['i']}|{times[w['i']]:.1f}s]{w['t']}" for w in kept if w["i"] in times),
        {"type": "object", "properties": {
            "quiz": {"type": "boolean"}, "fragen_gesamt": {"type": "integer"},
            "fragen": {"type": "array", "items": {"type": "object", "properties": {
                "frage_i": {"type": "integer"}, "aufloesung_i": {"type": "integer"}, "richtig": {"type": "boolean"}},
                "required": ["frage_i", "aufloesung_i", "richtig"]}}},
         "required": ["quiz", "fragen_gesamt", "fragen"]})
    if not res or not res.get("quiz"):
        return None
    qs = [q for q in res.get("fragen") or [] if q.get("frage_i") in times and q.get("aufloesung_i") in times]
    n = len(qs)  # Fragen, die im FERTIGEN Clip vorkommen (angekündigt „sechs“, geschnitten fünf → „/5“ wie beim Nutzer)
    if len(qs) < 2 or not 2 <= n <= 20:
        return None
    qs.sort(key=lambda q: times[q["frage_i"]])
    return {"n": n, "start": times[qs[0]["frage_i"]], "hits": [times[q["aufloesung_i"]] for q in qs if q.get("richtig")],
            "misses": [times[q["aufloesung_i"]] for q in qs if not q.get("richtig")]}


def quiz_events(q, total):
    """Großer Punktestand „k/N“ in der Bildmitte (wie beim Nutzer): steht ab der ersten Frage, springt bei jeder richtigen
    Antwort grün auf (Pop), wird danach wieder weiß. Liegt über den Untertiteln, nie über dem Gesicht."""
    ev, cy = [], caption_y()
    y = cy - int(CAPFS[0] * 1.35) - 70 if cy > 900 else cy + int(CAPFS[0] * 1.35) + 70
    fs = 190
    marks = [q["start"]] + q["hits"] + [total]
    for k in range(len(marks) - 1):
        a, b = marks[k], marks[k + 1]
        if b - a < 0.05:
            continue
        txt = f"{k}/{q['n']}"
        if k == 0:
            ev += text3d(txt, 540, y, a, b, fs, color=WHITE, anim=True, depth=6, fade=(120, 0))
        else:  # Treffer: grün + Pop für 0,9 s, dann weiß
            g = min(b, a + 0.9)
            ev += text3d(txt, 540, y, a, g, fs, color="&H0050E050", anim=True, depth=6, fade=(0, 0))
            if b > g:
                ev += text3d(txt, 540, y, g, b, fs, color=WHITE, anim=False, depth=6, fade=(0, 200 if k == len(marks) - 2 else 0))
    return ev


def probe_dur(path):
    return run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]).stdout.strip() or "0"


def out_at(t_src):
    """Zeit im fertigen Clip zu einer Zeit in der (zusammengesetzten) Quelle: über das nächste behaltene Wort danach."""
    cand = [w for w in WORDS if w["s"] >= t_src - 0.08 and w["i"] in OUT_TIME]
    return OUT_TIME[min(cand, key=lambda w: w["s"])["i"]] if cand else None


def sticker_png(src, alpha, path):
    """Produktbild als Sticker: freigestellt (PNG mit Transparenz) bleibt wie es ist; ein normales Foto bekommt runde Ecken
    und einen weißen Rand mit weichem Schatten (wie ein Foto-Sticker)."""
    from PIL import Image, ImageDraw, ImageFilter
    im = Image.open(src).convert("RGBA")
    wide = im.width > 1.6 * im.height  # Screenshot/Kommentar: breit darstellen, sonst unlesbar
    im.thumbnail((860, 860) if wide else (STICKER_PX, STICKER_PX))
    if alpha and im.getextrema()[3][0] < 250:  # wirklich transparent
        out = im
    else:
        b, r = 14, 36
        card = Image.new("RGBA", (im.width + 2 * b, im.height + 2 * b), (255, 255, 255, 255))
        m = Image.new("L", im.size, 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, im.width - 1, im.height - 1), r - b, fill=255)
        card.paste(im, (b, b), m)
        cm = Image.new("L", card.size, 0)
        ImageDraw.Draw(cm).rounded_rectangle((0, 0, card.width - 1, card.height - 1), r, fill=255)
        card.putalpha(cm)
        out = Image.new("RGBA", (card.width + 40, card.height + 40), (0, 0, 0, 0))
        sh = Image.new("RGBA", card.size, (0, 0, 0, 120))
        sh.putalpha(cm.point(lambda v: v * 120 // 255))
        out.paste(sh, (24, 28), sh)
        out = out.filter(ImageFilter.GaussianBlur(10))
        out.paste(card, (20, 20), card)
    out.save(path)
    return path


STICKER_PX = 430


def sticker_spot(sw, sh):
    """Regel 83 (Rückmeldung 05.10.): Sticker/Logo/Kommentar NIE über das Gesicht. Größter freier Bereich links/rechts
    neben dem Kopf, darüber oder darunter – ohne die Untertitel-Zeile und die Titel-Karte. Der Sticker wird passend
    verkleinert. Rückgabe (Faktor, x, y oben) oder None, wenn nirgends Platz ist (dann lieber weglassen)."""
    if FACES_OUT:
        ft, fb, fl, fr = face_box()
    else:
        ft, fb, fl, fr = 560, 1120, 300, 780
    cap, ch = caption_y(), CAPFS[0] * 0.75 + 40
    top_lim, bot_lim = max(430, int(LAY["trect"][3]) + 20 if LAY.get("trect") else 0), SAFE_B

    def free(x0, x1, y0, y1):  # Untertitel-Band aus dem Bereich herausnehmen → größerer Rest
        y0, y1 = max(y0, top_lim), min(y1, bot_lim)
        if cap + ch > y0 and cap - ch < y1:
            up, down = (y0, min(y1, cap - ch)), (max(y0, cap + ch), y1)
            y0, y1 = up if up[1] - up[0] >= down[1] - down[0] else down
        return x0, x1, y0, y1

    best = None
    for x0, x1, y0, y1 in (free(SAFE_L, fl, 0, 1920), free(fr, SAFE_BTN_X, 0, 1920), free(SAFE_L, SAFE_BTN_X, 0, ft - 20), free(SAFE_L, SAFE_BTN_X, fb + 20, 1920)):
        rw, rh = x1 - x0, y1 - y0
        if rw < 140 or rh < 140:
            continue
        f = min(1.0, rw / sw, rh / sh)
        if best is None or f > best[0]:
            best = (f, x0 + (rw - sw * f) / 2, y0 + (rh - sh * f) / 2)
    return best if best and best[0] >= 0.35 else None


def Image_wide(path):  # noqa: N802
    from PIL import Image
    w, h = Image.open(path).size
    return w > 1.5 * h  # Produktbild seitlich neben der Person (wie der schwebende Burger in den Malo's-Videos)


def burn(idx, cut, words_out, title, ranges=None, kept=None):
    """2. Durchgang: Untertitel (und bei Viral den Hook-Titel) einbrennen, web-sicheres MP4."""
    out = os.path.join(A.out, f"clip-{idx}.mp4")
    ass = os.path.join(A.out, f"clip-{idx}.ass")
    if LOUD and ranges and kept and idx not in FX:
        FX[idx] = gemini_effects(kept, OUT_TIME)
    pfx = [{**f, "t": OUT_TIME[f["i"]]} for f in FX.get(idx, []) if f["i"] in OUT_TIME] if LOUD else []
    caption_groups(words_out)  # legt die Schriftgröße fest – Grafiken/Emojis richten sich nach der Untertitel-Zeile
    logo = A.logo if A.logo and os.path.exists(A.logo) else None
    card = hook_card(title, os.path.join(A.out, f"titel-{idx}.png")) if title and not PREM else None
    LAY.clear()
    if not PREM:  # Regel 112: erst die Plätze vergeben, dann zeichnen (Premium vergibt sie in premium_events mit seinem 3D-Titel)
        from PIL import Image as _Im
        plan_layout(_Im.open(card).size if card else None, False)
        _fb = face_box()
        if card and LAY["title"] and _fb and LAY["cy"] < _fb[0] + 0.14 * (_fb[1] - _fb[0]) and _Im.open(card).size[1] > 150:
            # Untertitel stehen über dem Kopf (nahes Gesicht): zweizeiliger Titel würde sie in die Stirn drücken → flache Karte
            flach = hook_card(title, os.path.join(A.out, f"titel-{idx}.png"), flach=True)
            card = flach or hook_card(title, os.path.join(A.out, f"titel-{idx}.png"))
            plan_layout(_Im.open(card).size, False, force=True)
            card = card if LAY["title"] else None
        if card and not LAY["title"]:  # zweizeilig kein Platz → einzeilig und flacher versuchen, erst dann weglassen
            card = hook_card(title, os.path.join(A.out, f"titel-{idx}.png"), flach=True) or \
                hook_card(title, os.path.join(A.out, f"titel-{idx}.png"))  # zu lang für eine Zeile → zweizeilige Karte
            if card:
                STATS.pop("titel_weggelassen", None)
                plan_layout(_Im.open(card).size, False, force=True)
                card = card if LAY["title"] else None
                if card:
                    log("Titel einzeilig gesetzt – so passt er über den Kopf (Regel 112)")
    pev = premium_events(pfx, words_out, title, voll=PREM) if LOUD else []
    if ranges and kept and idx not in QUIZ:
        QUIZ[idx] = gemini_quiz(kept, OUT_TIME)
        STATS["quiz"] = QUIZ[idx]
    if QUIZ.get(idx):  # Straßen-Quiz: Punktestand wie in den Malo's-Videos des Nutzers
        pev += quiz_events(QUIZ[idx], sum(olen(s, e) for s, e in ranges))
    build_ass(words_out, ass, extra=pev, hide=list(HIDE) if LOUD else ())
    STATS["caption_words"] = len(words_out)
    esc = lambda p: p.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    vf = f"ass='{esc(ass)}':fontsdir='{esc(FONTS)}'"
    inputs, fc, amap, vmap = ["-i", cut], [], ["-map", "0:a", "-c:a", "copy"], None
    base, ni = "0:v", 1
    plan_snd = []
    total = float(probe_dur(cut))
    if PLAN:  # B-Rolls (Bild statt Sprecher, Ton läuft weiter) – UNTER den Untertiteln
        used = []
        for k, b in enumerate(PLAN.get("broll") or []):
            t0 = out_at(b["t_src"])
            if t0 is None or t0 > total - 0.8 or any(abs(t0 - u) < 2.5 for u in used) or not os.path.exists(b["file"]):
                continue
            d = min(b["dur"], total - t0 - 0.2, 3.0)
            if d < 0.8:
                continue
            used.append(t0)
            inputs += ["-i", b["file"]]
            fc.append(f"[{ni}:v]trim=0:{d:.3f},setpts=PTS-STARTPTS+{t0:.3f}/TB[br{k}]")
            fc.append(f"[{base}][br{k}]overlay=0:0:eof_action=pass[bv{k}]")
            base, ni = f"bv{k}", ni + 1
        STATS["broll"] = [round(u, 1) for u in used]
    fc.append(f"[{base}]{vf}[vb]")
    vin = "vb"
    hy, lx = LAY["hy"], LAY["lx"]
    if card:  # Titel oben: Viral gleitet sanft von oben rein, Clean blendet nur ruhig ein; bleibt 3 s
        inputs += ["-loop", "1", "-t", "3.3", "-i", card]
        fc.append(f"[{ni}:v]format=rgba,fade=t=in:st=0:d=0.3:alpha=1,fade=t=out:st=2.9:d=0.4:alpha=1[hc]")
        ypos = f"'{hy}-36*pow(1-clip(t/0.35,0,1),3)'" if LOUD else str(hy)
        fc.append(f"[{vin}][hc]overlay=x=(W-w)/2:y={ypos}:eof_action=pass[vh]")
        vin, ni = "vh", ni + 1
    if logo:
        # Inhaber 07.10.: Das Logo steht NICHT mehr dauerhaft oben links. Es wird eingeblendet, wenn der Name fällt („Golden
        # Thai“, „Novus Media“ – der erste Eintrag unter „Namen & Marken“), 2,6 s lang, höchstens dreimal. Fällt der Name nie,
        # erscheint es einmal zum Schluss. Platz: freie obere Ecke – nie auf Gesicht, Titel oder Untertiteln, sonst entfällt es.
        marke = [norm(x) for x in (A.words.split(",")[0] if A.words else "").split() if norm(x)]
        zeiten = []
        if marke:
            ws = [(norm(w[0]), w[1]) for w in words_out]
            for i in range(len(ws) - len(marke) + 1):
                if all(difflib.SequenceMatcher(a=ws[i + j][0], b=marke[j]).ratio() >= 0.8 for j in range(len(marke))):
                    if not zeiten or ws[i][1] - zeiten[-1] >= 6.0:
                        zeiten.append(ws[i][1])
        zeiten = [max(0.0, t - 0.15) for t in zeiten[:3] if t < total - 1.2] or [max(0.0, total - 2.8)]
        logo, lpad = logo_png(logo, os.path.join(A.out, f"logo-{idx}.png"))
        half_c, gezeigt = CAPFS[0] * 0.6 + 10, []
        for k, t0 in enumerate(zeiten):
            tr = LAY.get("trect") if t0 < 3.4 else None
            ecken = [(SAFE_L, SAFE_T), (SAFE_R - LOGO_PX, SAFE_T)]
            if tr:
                ecken = [(SAFE_L, int(tr[3]) + 14), (SAFE_R - LOGO_PX, int(tr[3]) + 14)] + ecken
            frei = [(x, y) for x, y in ecken
                    if not hits_face(x, y, x + LOGO_PX, y + LOGO_PX, t0, t0 + 2.6)
                    and not _cut(y, y + LOGO_PX, LAY["cy"] - half_c, LAY["cy"] + half_c)
                    and not (tr and _cut(x, x + LOGO_PX, tr[0], tr[2]) and _cut(y, y + LOGO_PX, tr[1] - 10, tr[3] + 10))
                    and y + LOGO_PX <= SAFE_B]
            if not frei:
                continue
            x, y = frei[0]
            inputs += ["-loop", "1", "-t", "2.6", "-i", logo]
            fc.append(f"[{ni}:v]format=rgba,scale={LOGO_PX + 2 * lpad}:-1:flags=lanczos,fade=t=in:st=0:d=0.3:alpha=1,"
                      f"fade=t=out:st=2.2:d=0.4:alpha=1,setpts=PTS+{t0:.3f}/TB[lg{k}]")
            fc.append(f"[{vin}][lg{k}]overlay=x={x - lpad}:y={y - lpad}:eof_action=pass[vl{k}]")
            vin, ni = f"vl{k}", ni + 1
            gezeigt.append(round(t0, 1))
        STATS["logo_zeiten"] = gezeigt
        log(f"Logo eingeblendet bei {gezeigt} s" + ("" if marke else " (kein Markenname hinterlegt → nur zum Schluss)") if gezeigt
            else "Logo weggelassen – beim Markennamen war keine Ecke frei")
    if PLAN and PLAN.get("bilder"):  # Produktbilder als Sticker: gleiten von unten rein, bleiben 1,8 s, Pop-Sound
        # NIE vor das Gesicht (Regel 83): freien Bereich neben/über/unter dem Kopf suchen, Sticker passend verkleinern
        from PIL import Image as _Img
        for k, b in enumerate(PLAN["bilder"]):
            t0 = out_at(b["t_src"])
            if t0 is None or t0 > total - 1.0 or not os.path.exists(b["file"]):
                continue
            png = sticker_png(b["file"], b.get("alpha"), os.path.join(A.out, f"sticker-{idx}-{k}.png"))
            spot = sticker_spot(*_Img.open(png).size)
            if not spot:
                log(f"Sticker {k + 1} weggelassen – kein Platz, ohne das Gesicht zu verdecken")
                continue
            f_, sx, sy = spot
            inputs += ["-loop", "1", "-t", f"{b['dur'] + 0.4:.2f}", "-i", png]
            fc.append(f"[{ni}:v]format=rgba,scale=trunc(iw*{f_:.3f}/2)*2:-2:flags=lanczos,fade=t=in:st=0:d=0.2:alpha=1,"
                      f"fade=t=out:st={b['dur']:.2f}:d=0.3:alpha=1,setpts=PTS+{t0:.3f}/TB[st{k}]")
            a = f"(60*pow(1-clip((t-{t0:.3f})/0.25,0,1),3))"
            fc.append(f"[{vin}][st{k}]overlay=x={int(sx)}:y='{int(sy)}+{a}':eof_action=pass[sv{k}]")
            vin, ni = f"sv{k}", ni + 1
            plan_snd.append((t0, "pop", 0.3))
    if LOUD and ranges and kept:
        if idx not in FX:
            FX[idx] = gemini_effects(kept, OUT_TIME)
        fx = [{**f, "t": OUT_TIME[f["i"]]} for f in FX[idx] if f["i"] in OUT_TIME]  # exakte Zeit im fertigen Clip
        snd = viral_sounds(ranges, fx)
        emo = [] if PREM else [f for f in fx if f["emoji"] and os.path.exists(os.path.join(EMOJI_DIR, f"{EMOJIS[f['emoji']]}.png"))]
        STATS.update(sfx=len(snd), fx=[(round(f["t"], 1), f["emoji"], f["sfx"]) for f in fx])
        # Emojis UNTER den Untertiteln, dezent (140 px), gleiten abwechselnd von links/rechts/unten/oben herein und wieder weg
        # Regel 112: jedes Emoji bekommt seinen Platz aus der gemeinsamen Platzvergabe (nie auf Untertitel, Titel, Logo, Gesicht,
        # nie unter den Knöpfen der Apps). Ist nirgends frei → lieber kein Emoji.
        spots = {id(f): emoji_spot(f["t"], f["t"] + 1.7) for f in emo}
        if any(v is None for v in spots.values()):
            STATS["emojis_weggelassen"] = sum(v is None for v in spots.values())
        emo = [f for f in emo if spots[id(f)]]
        STATS["emojis"] = len(emo)
        slide = [("-{a}", ""), ("+{a}", ""), ("", "+{a}"), ("", "-{a}")]
        for k, f in enumerate(emo):
            t0 = f["t"]
            ex, ey = spots[id(f)]
            inputs += ["-loop", "1", "-t", "1.7", "-i", os.path.join(EMOJI_DIR, f"{EMOJIS[f['emoji']]}.png")]
            fc.append(f"[{ni}:v]format=rgba,scale={EMOJI_PX}:{EMOJI_PX}:flags=lanczos,fade=t=in:st=0:d=0.25:alpha=1,"
                      f"fade=t=out:st=1.35:d=0.35:alpha=1,setpts=PTS+{t0:.3f}/TB[e{k}]")
            # Weg: rein = 80 px mit weichem Abbremsen, raus = 30 px weiter in dieselbe Richtung
            a = f"(80*pow(1-clip((t-{t0:.3f})/0.3,0,1),3)-30*pow(clip((t-{t0 + 1.35:.3f})/0.35,0,1),2))"
            sx, sy = (v.format(a=a) for v in slide[k % 4])
            fc.append(f"[{vin}][e{k}]overlay=x='{ex}-w/2{sx}':y='{ey}-h/2{sy}':eof_action=pass[vo{k}]")
            vin, ni = f"vo{k}", ni + 1
    else:
        snd = []
    if PREM and CARDS:  # Premium-Karten: gleiten beim gesprochenen Wort von unten herein, Stapel blendet gemeinsam aus
        for k, c in enumerate(CARDS):
            try:
                png, pad = card_png(c["text"], c.get("emoji"), os.path.join(A.out, f"karte-{idx}-{k}.png"))
            except Exception as e:  # noqa: BLE001
                log(f"Karte „{c['text']}“ ausgelassen ({e})")
                continue
            d = max(1.0, c["bis"] - c["t"])
            inputs += ["-loop", "1", "-t", f"{d + 0.3:.2f}", "-i", png]
            fc.append(f"[{ni}:v]format=rgba,fade=t=in:st=0:d=0.2:alpha=1,fade=t=out:st={d:.2f}:d=0.28:alpha=1,setpts=PTS+{c['t']:.3f}/TB[ka{k}]")
            fc.append(f"[{vin}][ka{k}]overlay=x=(W-w)/2:y='{c['y'] - pad}+46*pow(1-clip((t-{c['t']:.3f})/0.28,0,1),3)':eof_action=pass[kv{k}]")
            vin, ni = f"kv{k}", ni + 1
        STATS["premium_karten"] = [(round(c["t"], 1), c["text"]) for c in CARDS]
    snd += [(t, "ding", 0.3) for t in (QUIZ.get(idx) or {}).get("hits", [])]  # Quiz: Ding bei jeder richtigen Antwort
    snd += plan_snd
    snd = [x for x in snd if os.path.exists(os.path.join(SFX_DIR, f"{x[1]}.wav"))]
    if kept and idx not in MUSIC_PICK:
        MUSIC_PICK[idx] = pick_music(kept)
    mus = MUSIC_PICK.get(idx)
    if snd or mus:
        mix = []
        voice = "[0:a]"
        if mus:  # Musik: gleichmäßig laut, duckt automatisch unter die Stimme, weich rein/raus
            total = float(probe_dur(cut))
            inputs += ["-stream_loop", "-1", "-i", mus]
            fc.append("[0:a]asplit=2[vo][vk]")
            fc.append(f"[{ni}:a]aresample=48000,atrim=0:{total:.3f},asetpts=PTS-STARTPTS,loudnorm=I=-24:TP=-4:LRA=9,"
                      f"afade=t=in:d=0.4,afade=t=out:st={max(0.0, total - 1.2):.3f}:d=1.2[mraw]")
            fc.append("[mraw][vk]sidechaincompress=threshold=0.02:ratio=8:attack=30:release=450:makeup=1[mduck]")
            mix.append("[mduck]")
            voice = "[vo]"
            ni += 1
        for k, (t0, n, vol) in enumerate(snd):
            inputs += ["-i", os.path.join(SFX_DIR, f"{n}.wav")]
            ms = int(t0 * 1000)
            fc.append(f"[{ni}:a]aresample=48000,volume={vol},adelay={ms}|{ms}[s{k}]")
            mix.append(f"[s{k}]")
            ni += 1
        if mix:
            fc.append(f"{voice}{''.join(mix)}amix=inputs={len(mix) + 1}:normalize=0:duration=first,alimiter=limit=0.93:level=0[am]")
            amap = ["-map", "[am]", "-c:a", "aac", "-b:a", "192k"]
    fc.append(f"[{vin}]format=yuv420p[vf]")
    script = os.path.join(A.out, f"burn-{idx}.filter")
    open(script, "w").write(";\n".join(fc))
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-/filter_complex", script, "-map", "[vf]", *amap,
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-profile:v", "high", "-level", "4.1", "-color_range", "tv",
         "-movflags", "+faststart", out])
    if not A.keep_work:
        for f in [ass, script, os.path.join(A.out, f"titel-{idx}.png")] + [os.path.join(A.out, x) for x in os.listdir(A.out) if x.startswith(f"sticker-{idx}-")]:
            if os.path.exists(f):
                os.remove(f)
    return out


# ─────────────────────────────── 8. KI-Abnahme ───────────────────────────────
REVIEW_KINDS = ["untertitel", "fehlender_untertitel", "schnitt", "anfang", "ende", "bild", "ton", "titel", "effekte"]


def gemini_review(path, words_out, title):
    """Wie ein Chef-Cutter vor der Auslieferung: Die KI schaut sich den FERTIGEN Clip (Bild + Ton) an, gibt eine Note,
    listet Probleme und nennt falsch verstandene Untertitel. Kleine Vorschau-Datei (540 px), damit es schnell geht."""
    proxy = os.path.join(A.out, "abnahme.mp4")
    try:
        run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vf", "scale=540:-2,fps=12", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "33", "-c:a", "aac", "-b:a", "48k", "-ac", "1", proxy])
        if os.path.getsize(proxy) > 18e6:
            return None
        import base64
        video = {"inline_data": {"mime_type": "video/mp4", "data": base64.b64encode(open(proxy, "rb").read()).decode()}}
    finally:
        if os.path.exists(proxy) and not A.keep_work:
            os.remove(proxy)
    style = {"viral": "Viral – Hook-Titel-Karte oben, harte Zoom-Punches, Emojis unter den Untertiteln und Sounds sind GEWOLLT",
             "premium": "Premium – 3D-Titel oben, große animierte Schlüsselwörter und hochzählende Zahlen (Motion Graphics), "
                        "Zoom-Punches, Sounds, Kino-Look mit Vignette und feiner Körnung sind GEWOLLT",
             }.get(A.style, "Clean – ruhig und hochwertig; dezente Titel-Karte oben, kurze weiche Übergänge sind gewollt")
    caps = " ".join(f"[{s0:.1f}] {tx}" for tx, s0, _ in words_out)
    return gemini(
        "Du bist ein strenger Chef-Cutter für TikTok/Reels und machst die Abnahme, bevor ein zahlender Kunde sein Video bekommt. "
        f"Stil: {style}. Schau dir das fertige Kurzvideo komplett an, mit Ton, und prüfe: 1. Untertitel – stimmt der Text mit "
        "dem Gesprochenen (verhörte Wörter, Namen, fehlende Wörter)? Wird irgendwo gesprochen, ohne dass ein Untertitel steht? "
        "2. Schnitte – sichtbare Ruckler, Geisterbilder, abgehackte Wörter, unnatürliche Sprünge? 3. Anfang – packt er sofort? "
        "Ende – sauberer Abschluss, nichts abgeschnitten? 4. Bild (Wackeln, zu dunkel, Köpfe angeschnitten) und Ton (zu leise, "
        "zu laut, Knacken). 5. Titel und Effekte – passen sie? Nur echte, sichere Probleme – nichts erfinden, keine "
        "Geschmacksfragen. Untertitel-Korrekturen nur, wenn du das Gesprochene klar verstehst: „falsch“ = exakt der "
        "eingeblendete Text (ein bis fünf Wörter), „richtig“ = was wirklich gesagt wird. Wird irgendwo klar verständlich "
        "gesprochen, ohne dass ein Untertitel steht: unter „ergaenzen“ die Sekunden von/bis und den exakt gesprochenen Text "
        "(nur Wörter, die du sicher verstehst; Füllwörter wie äh/ähm weglassen). Note 1–10 = würdest du das Video "
        "nach deinen Untertitel-Korrekturen so an den Kunden freigeben (10 = perfekt, 7 = gut, unter 5 = nicht freigeben)?",
        f"Titel oben: {title or '–'}\n" + (f"Richtige Schreibweise von Namen/Marken im Video: {BRAND}\n" if BRAND else "") +
        f"Eingeblendete Untertitel mit Sekunde: {caps}",
        {"type": "object", "properties": {
            "note": {"type": "integer"}, "fazit": {"type": "string"},
            "probleme": {"type": "array", "items": {"type": "object", "properties": {
                "sek": {"type": "number"}, "art": {"type": "string", "enum": REVIEW_KINDS}, "was": {"type": "string"}},
                "required": ["sek", "art", "was"]}},
            "untertitel_korrekturen": {"type": "array", "items": {"type": "object", "properties": {
                "sek": {"type": "number"}, "falsch": {"type": "string"}, "richtig": {"type": "string"}},
                "required": ["sek", "falsch", "richtig"]}},
            "ergaenzen": {"type": "array", "items": {"type": "object", "properties": {
                "von": {"type": "number"}, "bis": {"type": "number"}, "text": {"type": "string"}},
                "required": ["von", "bis", "text"]}}},
         "required": ["note", "fazit", "probleme", "untertitel_korrekturen", "ergaenzen"]},
        parts=[video], timeout=120, deep=True)


# Ergänzungen der Abnahme („hier wird gesprochen, aber es steht kein Untertitel“) wurden bisher UNGEPRÜFT eingesetzt – bis zu
# 8 Wörter, die nur die Abnahme-KI „gehört“ hat. Dieselbe KI hat am 06.10. nachweislich ein Wort erfunden. AUS, bis jede
# Ergänzung von der lokalen Hörprobe (hear) bestätigt wird. Grundsatz: lieber kein Untertitel als ein falscher.
CAPTION_ADDS = False


def apply_caption_adds(words_out, adds, total):
    """Gesprochenes ohne Untertitel (von der Abnahme gehört) nachtragen – nur in echten Lücken (dort steht noch nichts),
    Wörter gleichmäßig über die Lücke verteilt, höchstens 8 Wörter je Stelle."""
    out, done = list(words_out), []
    if not CAPTION_ADDS:  # Regel 101: vorerst nichts einsetzen, nur festhalten (siehe CAPTION_ADDS)
        if adds:
            STATS.setdefault("ergaenzung_nicht_eingesetzt", []).extend(str(a.get("text", ""))[:60] for a in adds[:8])
        return out, done
    for a in adds or []:
        words = [w for w in str(a.get("text", "")).split() if norm(w) and norm(w) not in FILLER][:8]
        try:
            v, b = max(0.0, float(a["von"])), min(total, float(a["bis"]))
        except (KeyError, TypeError, ValueError):
            continue
        if not words or b - v < 0.25 * len(words) * 0.6 or b - v > 8:
            continue
        # freie Stelle innerhalb [v, b]: zwischen den vorhandenen Untertiteln
        busy = sorted((x[1], x[2]) for x in out if x[2] > v and x[1] < b)
        free, pos = [], v
        for s0, e0 in busy:
            if s0 - pos > 0:
                free.append((pos, s0))
            pos = max(pos, e0)
        if b - pos > 0:
            free.append((pos, b))
        if not free:
            continue
        fa, fb = max(free, key=lambda x: x[1] - x[0])
        if fb - fa < 0.18 * len(words):
            continue
        step = (fb - fa) / len(words)
        at = next((k for k, x in enumerate(out) if x[1] > fa), len(out))
        out[at:at] = [(w, fa + i * step, fa + (i + 1) * step) for i, w in enumerate(words)]
        done.append(f"{v:.0f}s „{' '.join(words)}“")
    return out, done


FUNKTIONSWOERTER = set("der die das den dem des ein eine einen einem einer eines kein keine keinen mein dein sein ihr im in am "
                       "an zum zur vom von zu bei mit auf aus und oder ist sind war hat haben wird".split())


def _schreibvariante(a, b):
    """b ist nur eine andere SCHREIBWEISE von a (Verhörer/Tippfehler desselben Worts) – kein anderes Wort."""
    if a == b:  # nur Groß-/Kleinschreibung oder Satzzeichen
        return True
    if a in FUNKTIONSWOERTER or b in FUNKTIONSWOERTER:  # Artikel & Co. zu ändern hieße Grammatik „verbessern“
        return False
    if min(len(a), len(b)) <= 3:  # bei Kurzwörtern macht ein einziger Buchstabe ein anderes Wort (ist/isst)
        return False
    # 0,72 statt 0,5: im Lauf vom 06.10. rutschte „geraten“ → „gebracht“ durch (Ähnlichkeit 0,67 – gleicher Wortrahmen, anderes
    # Wort). Echte Schreibvarianten liegen höher: Baglet/Baguelette 0,75, Schmarm/Schmarrn 0,80, Bürger/Burger 0,83.
    return difflib.SequenceMatcher(a=a, b=b).ratio() >= 0.72


def apply_caption_fixes(words_out, fixes):
    """Korrekturen aus der Abnahme einsetzen: Die falschen Wörter müssen genau so (±2,5 s) im Untertitel stehen; die
    richtigen Wörter übernehmen deren Zeitspanne. Vorsichtig: nur kleine, ähnliche Korrekturen – nie ganze Sätze umschreiben."""
    out, done = list(words_out), []
    for f in fixes or []:
        wrong = [norm(x) for x in str(f.get("falsch", "")).split() if norm(x)]
        right = str(f.get("richtig", "")).split()
        dw, dr = re.sub(r"\D", "", "".join(wrong)), re.sub(r"\D", "", " ".join(right))
        nums = [int(x) for x in wrong if x.isdigit()]
        # Zahl zerhackt verstanden: „neun-und-achtzig“ als „9, 80.“ → „89“ (Summe) bzw. dieselben Ziffern in anderer Reihenfolge
        same_digits = bool(dr) and (sorted(dr) == sorted(dw) or (len(nums) == len(wrong) > 1 and dr.isdigit() and sum(nums) == int(dr)))
        # Regel 99 (Testlauf 06.10.: Abnahme machte aus dem richtigen „11.05.“ ein „12.05.“ – falsches Veranstaltungsdatum
        # im Werbevideo): Sobald Ziffern im Spiel sind, darf nur UMFORMATIERT werden (gleiche Ziffern: „1105.“ → „11.05.“).
        # Ein anderes Datum, ein anderer Preis, eine andere Uhrzeit wird nie übernommen – die KI hört es nicht besser als der Ton.
        if (dw or dr) and not same_digits:
            STATS.setdefault("zahl_korrektur_abgelehnt", []).append(f"{f.get('falsch')} → {f.get('richtig')}"[:80])
            continue
        # Regel 101 (Testlauf 06.10.: Abnahme schrieb „Richtig lecker.“ – gesprochen wird „Mhh, lecker“): Die Abnahme darf
        # im Untertitel nur die SCHREIBWEISE eines vorhandenen Worts berichtigen (Baglet → Baguelette, Bürger → Burger).
        # Keine Wörter dazu oder weg, keine anderen Wörter, keine Grammatik „verbessern“ (der/das/dem bleiben, wie gesagt).
        rn = [norm(x) for x in right if norm(x)]
        if not wrong or not rn or len(wrong) > 5:
            continue
        if not same_digits and (len(rn) != len(wrong) or not all(_schreibvariante(a, b) for a, b in zip(wrong, rn))):
            STATS.setdefault("korrektur_abgelehnt", []).append(f"{f.get('falsch')} → {f.get('richtig')}"[:80])
            continue
        t = float(f.get("sek", -99))
        for k in range(len(out) - len(wrong) + 1):
            if abs(out[k][1] - t) > 2.5 or [norm(x[0]) for x in out[k:k + len(wrong)]] != wrong:
                continue
            a, b = out[k][1], out[k + len(wrong) - 1][2]
            step = (b - a) / len(right)
            out[k:k + len(wrong)] = [(w, a + i * step, a + (i + 1) * step) for i, w in enumerate(right)]
            done.append(f"{f['falsch']} → {f['richtig']}")
            break
    return out, done


LOG = os.environ.get("VELOX_JOBLOG", os.path.join(MASCHINE, "logs", "jobs.jsonl"))


# ─────────────────────────────── 8b. Schnitt-Reparatur (Satzgrenzen, Fetzen, Nachschnitt nach der Abnahme) ───────────────────────────────
def fix_bounds(m):
    """Nie mitten im Satz beginnen oder enden: Ende bis zum nächsten Satzende verlängern (max. 8 s), Anfang auf den
    Satzanfang zurücksetzen (max. 5 s). Test 07: Clip endete bei „eine 10 von …“."""
    a, b = m["start"], m["end"]
    if not SENT_END.search(WORDS[b]["t"]):
        j = b
        while j + 1 < len(WORDS) and not SENT_END.search(WORDS[j]["t"]) and WORDS[j + 1]["e"] - WORDS[b]["e"] <= 8.0:
            j += 1
        if SENT_END.search(WORDS[j]["t"]) and j != b:
            log(f"Satzgrenze: Ende bis „{WORDS[j]['t']}“ verlängert (+{WORDS[j]['e'] - WORDS[b]['e']:.1f} s)")
            b = j
    if a > 0 and not re.search(r"[.!?…:]$", WORDS[a - 1]["t"]):
        j = a
        while j > 0 and not re.search(r"[.!?…:]$", WORDS[j - 1]["t"]) and WORDS[a]["s"] - WORDS[j - 1]["s"] <= 5.0:
            j -= 1
        if j != a and (j == 0 or re.search(r"[.!?…:]$", WORDS[j - 1]["t"])):
            log(f"Satzgrenze: Anfang auf „{WORDS[j]['t']}“ zurückgesetzt")
            a = j
    return {**m, "start": a, "end": b}


def edge_junk(heard, words_out, secs):
    """Hörprobe: Ist VOR dem ersten oder NACH dem letzten Untertitel noch Sprache zu hören (abgehackter Wortfetzen wie
    „Kennt ihr sch-“), wie viel Sekunden am Anfang/Ende wegschneiden? (0, 0) = sauber."""
    if not words_out or not heard:
        return 0.0, 0.0
    first, last = words_out[0][1], words_out[-1][2]
    lead = [h for h in heard if h["e"] <= first - 0.04 and h["e"] - h["s"] >= 0.08 and norm(h["t"])]
    tail = [h for h in heard if h["s"] >= last + 0.04 and h["e"] - h["s"] >= 0.08 and norm(h["t"])]
    cut_a = max(0.0, first - 0.06) if lead else 0.0
    cut_b = max(0.0, secs - (last + 0.3)) if tail else 0.0  # 0,3 s Luft: Untertitel-Ende liegt oft vor dem Wortende
    if lead or tail:
        STATS["edge_junk"] = [h["t"] for h in lead + tail][:6]
    return cut_a, cut_b


CUT_KINDS = ("schnitt", "anfang", "ende")


def gemini_fix_story(m, kept, problems):
    """Die Abnahme hat Schnittfehler gefunden (abgehackt, mitten im Satz, unnötige Pause …): die KI korrigiert die
    Satzauswahl mit genau diesem Hinweis – wie ein Cutter, der die Anmerkung vom Chef umsetzt."""
    # Test 07.10.: Der Nachschnitt strich den Schlusssatz („So sieht die Junior Box bei Malo's aus … Capri-Sonne“), weil die
    # Kontrolle die AUSSPRACHE von Namen bemängelte. Aussprache, Dialekt und Grammatik des Sprechers sind kein Schnittfehler –
    # solche Befunde führen nie zu einem Nachschnitt (die richtige Schreibweise steht ohnehin im Untertitel).
    problems = [p for p in problems if not re.search(r"aussprach|ausgesprochen|dialekt|grammatik|akzent|betonung", f"{p.get('art')} {p.get('was')}".lower())]
    if (PLAN or {}).get("zeigen"):  # stumme Bildfolge (Regel 114) ist gewollt – „lange Lücke/Pause ohne Sprache“ ist dort kein Fehler
        problems = [p for p in problems if not re.search(r"lücke|pause|stille|ohne sprache|kein ton", f"{p.get('art')} {p.get('was')}".lower())]
    if not problems:
        return None
    lo = max(0, m["start"] - 80)
    hi = min(len(WORDS) - 1, m["end"] + 80)
    sents = sentences(WORDS[lo:hi + 1])
    kept_i = {w["i"] for w in kept}
    lines = []
    for k, st in enumerate(sents):
        ins = [w for w in st if w["i"] in kept_i]
        t = OUT_TIME.get(ins[0]["i"]) if ins else None
        tag = f"[{k}|+|{t:.1f}s]" if ins and t is not None else f"[{k}|−]"
        lines.append(f"{tag} {' '.join(w['t'] for w in st)}")
    probs = "\n".join(f"- bei {p.get('sek', 0):.0f} s ({p.get('art')}): {p.get('was')}" for p in problems)
    res = gemini(
        CUTTER + "Die Abnahme des fertigen Videos hat Schnittfehler gefunden. Korrigiere die Satzauswahl so, dass genau diese "
        "Fehler behoben sind – sonst nichts ändern. Bemängelt die Abnahme einen Pannen-Satz, Halbsatz oder eine Zwischenbemerkung "
        "(„plötzlich heißt es ‚das Fleisch verbrannt‘“), dann diesen Satz ENTFERNEN – niemals durch mehr Drumherum "
        "„vervollständigen“ (Test 06.10.: der Nachschnitt nahm die ganze Pannen-Passage dazu, das Video wurde länger und schlechter). "
        "Begrüßung und Schlusssatz/Verabschiedung bleiben immer im Video. Wie jemand einen Namen ausspricht, ist nie ein Grund, einen Satz zu streichen. "
        "+ = Satz ist im Video (mit Sekunde im fertigen Video), − = rausgeschnitten. "
        + ("WICHTIG – dies ist eine Zubereitung/Anleitung: Ein Arbeitsschritt (etwas wird draufgelegt, gewendet, mit Soße bestrichen, "
           "eingepackt …) wird NIE entfernt, auch wenn der Satz holprig, kurz oder undeutlich gesprochen ist – der Zuschauer muss "
           "jeden Schritt sehen (Inhaber 06.10.: „du hast viel zu viel rausgenommen“). Entfernen darfst du hier nur Pannen-Sätze, "
           "Fehlstarts und Doppeltes. "
           if re.search(r"zubereit|anleitung|tutorial|rezept|so wird|so entsteht|behind", str((PLAN or {}).get("format") or "").lower()) else "")
        + STORY_RULES + " Antworte mit den Nummern ALLER Sätze, die ins korrigierte Video gehören (in Reihenfolge).",
        f"Befunde der Abnahme:\n{probs}\n\nSätze:\n" + "\n".join(lines),
        {"type": "object", "properties": {"keep": {"type": "array", "items": {"type": "integer"}}, "warum": {"type": "string"}},
         "required": ["keep"]}, deep=True)
    keep = sorted({k for k in (res or {}).get("keep") or [] if isinstance(k, int) and 0 <= k < len(sents)})
    if not keep:
        return None
    # Feste Sicherung (Test 07.10.: zweimal strich der Nachschnitt den Schlusssatz mit der Capri-Sonne): Der erste und der
    # letzte Satz, die im Video sind, bleiben immer drin – Begrüßung und Schluss streicht keine Kontrolle.
    drin = [k for k, st in enumerate(sents) if any(w["i"] in kept_i for w in st)]
    if drin:
        keep = sorted(set(keep) | {drin[0], drin[-1]})
    # Zweite feste Sicherung (Ordner-Test des Inhabers 07.10.: „Die Gewinner sind die drei Top-Kommentare unter diesem Video“
    # fiel komplett weg, weil darin ein Stotterer bemängelt wurde): Der Nachschnitt darf einen Satz, der im Video ist, nur dann
    # streichen, wenn er DOPPELT ist (sein Inhalt steht in einem anderen behaltenen Satz) oder eine Panne bespricht. Einen
    # Satz mit eigenem Inhalt streicht keine Kontrolle – „ein paar Sachen hat er einfach weggelassen, die wichtig wären“.
    inhalt = lambda st: {norm(w["t"]) for w in st if len(norm(w["t"])) >= 4}
    zurueck = []
    for k in drin:
        if k in keep:
            continue
        a_ = inhalt(sents[k])
        rest = set().union(*[inhalt(sents[j]) for j in keep]) if keep else set()
        panne = re.search(r"verbrannt|angebrannt|ist falsch|falsch gemacht|oh no|nochmal|warte", " ".join(w["t"] for w in sents[k]).lower())
        if a_ and len(a_ & rest) / len(a_) < 0.6 and not panne:
            zurueck.append(k)
    if zurueck:
        keep = sorted(set(keep) | set(zurueck))
        log(f"Nachschnitt: {len(zurueck)} Satz/Sätze mit eigenem Inhalt bleiben im Video (nur Doppeltes und Pannen dürfen raus)")
    new = {"start": sents[keep[0]][0]["i"], "end": sents[keep[-1]][-1]["i"], "title": m.get("title", ""),
           "drop": {w["i"] for k in range(keep[0], keep[-1] + 1) if k not in keep for w in sents[k]}}
    if new["start"] == m["start"] and new["end"] == m["end"] and new["drop"] == (m.get("drop") or set()):
        return None
    log(f"Nachschnitt: {(res or {}).get('warum', '')[:140]}")
    return new


def sense_check(path, c):
    """Regel 87 (Kollege des Nutzers, 05.10.): Zum Schluss das FERTIGE Video abhören (Whisper) und mit dem Rohmaterial
    vergleichen – ergibt es für einen Zuschauer ohne Vorwissen Sinn? Reihenfolge, fehlende Antworten/Schritte/Auflösung,
    Sprünge, abgebrochene Sätze, Versprecher, Füllwörter, Doppeltes, Schluss. Liefert Probleme im Format der Abnahme."""
    heard = hear(path)
    if not heard:
        return None
    fertig = " ".join(f"[{h['s']:.0f}s] {h['t']}" if k % 8 == 0 else h["t"] for k, h in enumerate(heard))
    m, kept_i = c["m"], {w["i"] for w in c["kept"]}
    lo, hi = max(0, m["start"] - 80), min(len(WORDS) - 1, m["end"] + 80)
    roh = "\n".join(("+ " if any(w["i"] in kept_i for w in st) else "− ") + " ".join(w["t"] for w in st)
                    for st in sentences(WORDS[lo:hi + 1]))
    res = gemini(
        CUTTER + "Letzte Kontrolle vor der Auslieferung. Du bekommst (1) das Rohmaterial Satz für Satz (+ = im Video, "
        "− = rausgeschnitten) und (2) was im FERTIGEN Video tatsächlich zu hören ist. Prüfe wie ein strenger Chef-Cutter: "
        "Ergibt das Video für einen Zuschauer ohne Vorwissen Sinn? Stimmt die Reihenfolge? Fehlt etwas Wichtiges (Antwort auf "
        "eine gestellte Frage, Auflösung, Bewertung, Preis, ein Schritt der Zubereitung), das im Rohmaterial vorhanden ist? Gibt "
        "es Sprünge, abgebrochene Sätze, Satzanfänge, die mitten drin weggeschnitten werden, Ankündigungen ohne Fortsetzung („wenn das Brot fertig ist“ – und dann geht es um etwas anderes), Versprecher, Füllwörter, doppelte Aussagen, Wortfetzen oder einen Schluss, der nicht "
        "passt? Nenne nur echte Probleme mit Sekunde im fertigen Video. ok=true, wenn das Video so raus kann.",
        f"ROHMATERIAL:\n{roh}\n\nFERTIGES VIDEO (zu hören):\n{fertig}",
        {"type": "object", "properties": {"ok": {"type": "boolean"}, "note": {"type": "integer"},
                                          "probleme": {"type": "array", "items": {"type": "object", "properties": {
                                              "sek": {"type": "number"}, "was": {"type": "string"}}, "required": ["sek", "was"]}}},
         "required": ["ok", "probleme"]}, deep=True)
    if not res:
        return None
    return {"ok": bool(res.get("ok")), "note": res.get("note"),
            "probleme": [{"sek": float(p.get("sek") or 0), "art": "schnitt", "was": str(p.get("was") or "")[:160]}
                         for p in res.get("probleme") or [] if p.get("was")][:6]}


def quality_check(path, words, kept, orig, secs):
    """Sicherheits-Check: Ein Clip wird nur ausgeliefert, wenn wirklich geschnitten und untertitelt wurde.
    Schlägt er fehl, bricht die Pipeline ab → Auftrag „fehlgeschlagen“, Credits werden automatisch erstattet."""
    t0, t1 = words[0]["s"], words[-1]["e"]
    pauses = sum(min(b, t1) - max(a, t0) for a, b in SIL if b > t0 and a < t1 and b - a > 0.4)
    removable = pauses + 0.3 * STATS.get("filler", 0)
    removed = orig - secs
    problems = []
    # erwartet: jedes gesprochene Wort – ohne reine Satzzeichen und ohne bewusst weggelassene unverständliche Stellen;
    # bei sehr kurzen Clips darf ein einzelnes Wort (z. B. ein leises „Mh“) fehlen
    expected = [w for w in kept if norm(w["t"]) and not w.get("unclear")]
    got = STATS.get("caption_words", 0)
    if expected and got < len(expected) * 0.85 and len(expected) - got > 1:
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
    if SHORT or sum(olen(s, e) for s, e in ranges) <= 60.5:
        return ranges, kept
    acc, cut_at = 0.0, None
    for s, e in ranges:
        for w in (w for w in kept if s <= w["e"] <= e + 0.1):
            if acc + (w["e"] - s) / SPEED[0] > 60:
                break
            if SENT_END.search(w["t"]):
                cut_at = w["e"] + 0.12
        acc += olen(s, e)
        if acc > 60:
            break
    if not cut_at:
        return ranges, kept
    return [[s, min(e, cut_at)] for s, e in ranges if s < cut_at], [w for w in kept if w["e"] <= cut_at]


results = []


def _ruhe(t, vor, maxd):
    """Vom Zeitpunkt t aus (vor=True: rückwärts, sonst vorwärts) die nächste ruhige Stelle suchen: dem Pegel folgen, solange
    er fällt; sobald er nach dem Tiefpunkt wieder um 5 dB steigt, beginnt ein ANDERES Geräusch – dort nicht hinüber.
    Rückgabe: Zeit des Tiefpunkts (höchstens maxd entfernt)."""
    i0 = min(len(LV) - 1, max(0, int(t / 0.05)))
    best, bi = LV[i0], i0
    for d in range(1, int(maxd / 0.05) + 1):
        i = i0 - d if vor else i0 + d
        if i < 0 or i >= len(LV):
            break
        if LV[i] < best:
            best, bi = LV[i], i
        elif LV[i] > best + 5 and d >= 2:
            break
    return bi * 0.05 + 0.025


def trim_joins(ranges, kept, joins):
    """Regel 109 (Inhaber 06.10.: vor „Ihr bekommt bei uns“ hört man ein „Ja“ vom Kameramann; bei der Junior Box setzt das
    Bild erst ein, wenn er schon redet). An einer Clip-Grenze gehören Nachlauf des einen und Vorlauf des nächsten Clips nicht
    zusammen. Der Abschnitt davor endet in der ersten Ruhe nach seinem letzten Wort (fremde Laute danach fallen weg), der
    Abschnitt danach beginnt in der letzten Ruhe vor seinem ersten Wort (man sieht die Person ansetzen, hört aber nichts Fremdes)."""
    out, n_cut = [], 0.0
    # Sicherung: gekürzt wird nur an einer ECHTEN Ruhe-Stelle (mind. 12 dB unter Sprachlautstärke). Liegt zwischen dem Wort
    # und dem Rand durchgehend Schall, bleibt alles stehen – es könnte ein Wortanfang sein, den die Erkennung verschluckt hat.
    laut = sorted(LV)[int(len(LV) * 0.9)] if LV else 0.0
    lv = lambda t: LV[min(len(LV) - 1, max(0, int(t / 0.05)))]
    still = lambda t: lv(t) <= laut - 12
    # Eng gefasst (Test am Malo's-Rohschnitt: ohne diese Grenzen fielen 14 s wortlose HANDLUNG weg): gekürzt wird nur ein
    # KURZER Rand (≤ 0,8 s zwischen Wort und Clip-Grenze), in dem wirklich ein Geräusch liegt (≥ 8 dB über der Ruhe-Stelle).
    # Lange wortlose Strecken sind Handlung oder Schwenk – dafür gelten die Regeln 96/97/107, nicht diese.
    # Inhaber 07.10.: „an den Wechseln schneller schneiden – der minimale Leerraum, in dem nichts gesagt wird, muss weg“.
    # Gekürzt wird nur an ANHALTENDER Ruhe (mind. 0,15 s) – eine kurze Lautpause mitten im Wort („Ma-tcha“) zählt nicht.
    lang_still = lambda t: still(t) and still(t + 0.05) and still(t + 0.1)
    fremd = lambda t0, t1, tief: t1 - t0 > 0.04 and max(lv(t0 + k * 0.05) for k in range(int((t1 - t0) / 0.05) + 1)) >= lv(tief) + 8
    for a, b in ranges:
        a0, b0 = a, b
        if LV and any(abs(b - (j - 0.02)) < 0.03 for j in joins):   # endet an einer Clip-Grenze
            last = max((w["e"] for w in kept if a <= w["s"] < b), default=None)
            if last is not None and 0.25 < b - last <= 0.8:
                t = _ruhe(last, False, min(0.5, b - last))
                if still(t) and fremd(t + 0.05, b, t):
                    b = min(b, max(last + 0.08, t + 0.03))
                elif lang_still(t):   # kein fremder Laut, aber echte Ruhe: nicht weiter zeigen, wie die Person „wegwischt“
                    b = min(b, max(last + 0.18, t + 0.10))
        if LV and any(abs(a - (j + 0.02)) < 0.03 for j in joins):   # beginnt an einer Clip-Grenze
            first = min((w["s"] for w in kept if a - 0.05 <= w["s"] < b), default=None)
            if first is not None and 0.2 < first - a <= 0.8:
                t = _ruhe(first, True, min(0.45, first - a))
                if still(t) and fremd(a, t - 0.05, t):
                    a = max(a, min(first - 0.05, t - 0.03))
                elif still(t) and still(t - 0.05) and still(t - 0.1):
                    a = max(a, min(first - 0.10, t - 0.08))
        if b - a > 0.08:
            out.append([a, b])
            n_cut += (a - a0) + (b0 - b)
    STATS["clipgrenzen_gekuerzt_s"] = round(n_cut, 2)
    return out


def split_joins(ranges, kept):
    """Clip-Grenze mitten in einem Abschnitt → dort trennen (eigener Ausschnitt je Clip), danach Regel 109."""
    if not (PLAN and PLAN.get("joins")):
        return ranges
    split = []
    for a, b in ranges:
        for j in sorted(PLAN["joins"]):
            if a + 0.15 < j < b - 0.15:
                split.append([a, j - 0.02])
                a = j + 0.02
        split.append([a, b])
    return trim_joins(split, kept, PLAN["joins"])


def make_cut(n, m, step):
    """Schnitt eines Moments bis zum fertigen Schnitt-Video (ohne Untertitel): Schnittliste, Rendern, Hörprobe
    (sicherer Neuschnitt bei angeschnittenen Wörtern), Fetzen am Anfang/Ende wegschneiden."""
    words = WORDS[m["start"]:m["end"] + 1]
    ranges, kept = keep_ranges(words, extra_drop=m.get("drop") or set())
    if not ranges:
        return None
    choose_speed(kept)
    ranges = split_joins(ranges, kept)
    ranges, kept = limit60(ranges, kept)
    ranges = tidy(ranges)
    if os.environ.get("VELOX_DEBUG"):  # Fehlersuche: Wörter (mit „raus“) und Schnittliste ablegen
        kid = {w["i"] for w in kept}
        json.dump({"words": [[w["t"], round(w["s"], 2), round(w["e"], 2), w["i"] in kid] for w in words],
                   "ranges": [[round(a, 2), round(b, 2)] for a, b in ranges], "sil": SIL},
                  open(os.path.join(A.out, f"debug-{n}.json"), "w"), ensure_ascii=False)
    step(0.2, "Setzt die Schnitte und glättet die Übergänge …")
    cut, secs = render_cut(n, ranges)
    step(0.55, "Prüft den Schnitt (Hörprobe) …")
    heard = hear(cut)
    words_out, miss = align(ranges, kept, heard)
    STATS["missing_first"] = miss[:20]
    if len(miss) > max(2, len(kept) * 0.03):  # Wörter angeschnitten → vorsichtiger neu schneiden
        log(f"Hörprobe: {len(miss)} Wörter unklar ({', '.join(miss[:6])}) – schneide im sicheren Modus neu")
        step(0.6, "Schneidet eine Stelle vorsichtiger nach …")
        ranges, kept = keep_ranges(words, safe=True, extra_drop=m.get("drop") or set())
        # Fehler bis 06.10.: im sicheren Modus fehlte die Trennung an den Clip-Grenzen (und damit Regel 109) – bei Malo's
        # lief zweimal genau dieser Pfad, die Abschnitte gingen dann wieder über Clip-Wechsel hinweg.
        ranges, kept = limit60(split_joins(ranges, kept), kept)
        ranges = tidy(ranges)
        if os.environ.get("VELOX_DEBUG"):  # Fehlersuche: auch die Schnittliste des sicheren Modus ablegen
            kid = {w["i"] for w in kept}
            json.dump({"words": [[w["t"], round(w["s"], 2), round(w["e"], 2), w["i"] in kid] for w in words],
                       "ranges": [[round(a, 2), round(b, 2)] for a, b in ranges], "sil": SIL,
                       "joins": (PLAN or {}).get("joins"), "lv": [round(v, 1) for v in LV]},
                      open(os.path.join(A.out, f"debug-{n}-sicher.json"), "w"), ensure_ascii=False)
        cut, secs = render_cut(n, ranges)
        heard = hear(cut)
        words_out, miss = align(ranges, kept, heard)
        STATS["safe_mode"] = True
    ca, cb = edge_junk(heard, words_out, secs)
    if (ca > 0.05 and ranges[0][1] - ranges[0][0] - ca > 0.4) or (cb > 0.05 and ranges[-1][1] - ranges[-1][0] - cb > 0.4):
        log(f"Hörprobe: Wortfetzen am {'Anfang' if ca else 'Ende'} ({', '.join(STATS.get('edge_junk', []))}) – wird weggeschnitten")
        if ca > 0.05 and ranges[0][1] - ranges[0][0] - ca > 0.4:
            ranges[0][0] += ca
        if cb > 0.05 and ranges[-1][1] - ranges[-1][0] - cb > 0.4:
            ranges[-1][1] -= cb
        ranges = tidy(ranges)
        cut, secs = render_cut(n, ranges)
        words_out, miss = align(ranges, kept, hear(cut))
    STATS["missing"] = miss[:20]
    return {"m": m, "words": words, "ranges": ranges, "kept": kept, "cut": cut, "secs": secs, "words_out": words_out}


def caption_round(rev, words_out, secs):
    """Untertitel-Korrekturen und fehlende Untertitel aus einer Abnahme übernehmen."""
    words_out, fixed = apply_caption_fixes(words_out, rev.get("untertitel_korrekturen"))
    words_out, added = apply_caption_adds(words_out, rev.get("ergaenzen"), secs)
    return words_out, fixed + [f"ergänzt: {x}" for x in added]


def reapply_fixes(words_out, revs):
    """Regel 104: Untertitel-Korrekturen, die die Abnahme schon gebracht hat, nach einem NEUEN Schnitt erneut einsetzen.
    (Fehler bis 06.10.: Der Nachschnitt nach dem Sinn-Check brannte die frischen, unkorrigierten Untertitel ein – im Protokoll
    stand „korrigiert“, im Video „Vollmilzschokolade“.) Die Zeiten der Vorschläge gelten für den alten Schnitt, deshalb wird
    jede Stelle im neuen Text gesucht; ob eine Korrektur zulässig ist, entscheidet wieder apply_caption_fixes (Regeln 99/101)."""
    out, done = list(words_out), []
    for rev in revs:
        for f in (rev or {}).get("untertitel_korrekturen") or []:
            wrong = [norm(x) for x in str(f.get("falsch", "")).split() if norm(x)]
            k = next((k for k in range(len(out) - len(wrong) + 1) if wrong and [norm(x[0]) for x in out[k:k + len(wrong)]] == wrong), None)
            if k is None:
                continue
            out, d = apply_caption_fixes(out, [dict(f, sek=out[k][1])])
            done += d
    return out, done


probs_of = lambda rev: [f"{p.get('sek', 0):.0f}s {p.get('art')}: {(p.get('was') or '')[:120]}" for p in (rev.get("probleme") or [])]

N = len(MOMENTS)
for n, m in enumerate(MOMENTS, 1):
    base = 45 + 50 * (n - 1) / N
    step = lambda frac, text: stage(text + (f" (Clip {n}/{N})" if N > 1 else ""), base + 50 / N * frac)
    m = fix_bounds(m)
    c = make_cut(n, m, step)
    if not c:
        continue
    title = (m.get("title") or "").strip()
    card_title = gemini_hook(c["kept"]) or title  # Titel oben im Bild (Viral und Clean)
    title = title or card_title
    if not title:
        first = next((s for s in sentences(c["kept"]) if len(s) >= 3), c["kept"][:8])
        title = " ".join(w["t"] for w in first[:7]).rstrip(",.;:")
    step(0.8, "Setzt die Untertitel …")
    f = burn(n, c["cut"], c["words_out"], card_title, c["ranges"], c["kept"])
    step(0.9, "KI-Abnahme: schaut sich den fertigen Clip an …")
    rev = None if A.no_review else gemini_review(f, c["words_out"], card_title)
    if rev:
        log(f"KI-Abnahme: Note {rev.get('note')}/10 – {(rev.get('fazit') or '')[:200]}")
        STATS["abnahme"] = {"note": rev.get("note"), "fazit": (rev.get("fazit") or "")[:200], "korrigiert": [], "probleme": probs_of(rev)[:12]}
        cutp = [p for p in rev.get("probleme") or [] if p.get("art") in CUT_KINDS]
        m2 = gemini_fix_story(c["m"], c["kept"], cutp) if cutp else None
        if m2:  # Schnittfehler → mit dem Hinweis der Abnahme neu schneiden (einmal), dann neu prüfen
            step(0.85, "Verbessert den Schnitt nach der KI-Abnahme …")
            c2 = make_cut(n, fix_bounds(m2), step)
            if c2:
                if c["cut"] != c2["cut"] and os.path.exists(c["cut"]) and not A.keep_work:
                    os.remove(c["cut"])  # (gleicher Dateiname = schon überschrieben – NICHT löschen)
                c = c2
                QUIZ.pop(n, None)  # Quiz-Zeiten neu (Effekte hängen an Wort-Nummern und bleiben gültig)
                f = burn(n, c["cut"], c["words_out"], card_title, c["ranges"], c["kept"])
                STATS["abnahme"]["nachschnitt"] = [p.get("was", "")[:100] for p in cutp][:4]
                rev = gemini_review(f, c["words_out"], card_title) or rev
                STATS["abnahme"]["note_nachschnitt"] = rev.get("note")
                log(f"KI-Abnahme nach Nachschnitt: Note {rev.get('note')}/10")
        revs_done = [rev]
        words_out, fixed = caption_round(rev, c["words_out"], c["secs"])
        if fixed:
            STATS["abnahme"]["korrigiert"] = fixed
            log(f"KI-Abnahme: {len(fixed)} Untertitel korrigiert ({'; '.join(fixed[:4])}) – brennt neu ein")
            f = burn(n, c["cut"], words_out, card_title, c["ranges"], c["kept"])
            rev2 = gemini_review(f, words_out, card_title)  # Endnote nach den Korrekturen
            if rev2:
                STATS["abnahme"].update(note_nach=rev2.get("note"), probleme_nach=probs_of(rev2)[:8])
                log(f"KI-Abnahme nach Korrektur: Note {rev2.get('note')}/10")
                revs_done.append(rev2)
                words_out, fixed2 = caption_round(rev2, words_out, c["secs"])
                if fixed2:  # zweite (letzte) Korrektur-Runde – danach keine weitere Prüfung, damit es nicht endlos läuft
                    STATS["abnahme"]["korrigiert"] += fixed2
                    log(f"KI-Abnahme Runde 2: {len(fixed2)} weitere Korrekturen – brennt neu ein")
                    f = burn(n, c["cut"], words_out, card_title, c["ranges"], c["kept"])
    if not A.no_review:  # Regel 87: fertiges Video abhören, Sinn gegen das Rohmaterial prüfen, bei Bedarf nachschneiden
        step(0.93, "Hört das fertige Video ab und prüft, ob alles Sinn ergibt …")
        sc = sense_check(f, c)
        if sc:
            STATS["sinn"] = {"ok": sc["ok"], "note": sc.get("note"), "probleme": [p["was"][:100] for p in sc["probleme"]]}
            log(f"Sinn-Check: {'ok' if sc['ok'] else 'Probleme'} – " + "; ".join(p["was"][:80] for p in sc["probleme"][:3]))
            m3 = gemini_fix_story(c["m"], c["kept"], sc["probleme"]) if not sc["ok"] and sc["probleme"] else None
            if m3:
                step(0.94, "Verbessert den Schnitt nach dem Sinn-Check …")
                c3 = make_cut(n, fix_bounds(m3), step)
                # Regel 95: Der Nachschnitt darf glätten, aber nicht das halbe Video wegwerfen (Test 05.10.: 86 s → 35 s,
                # die ganze Zubereitung fehlte). Wird es mehr als 15 % kürzer, bleibt die geprüfte Fassung.
                if c3 and c3["secs"] < 0.85 * c["secs"]:
                    log(f"Sinn-Check: Nachschnitt verworfen ({c['secs']} s → {c3['secs']} s wäre zu viel Verlust)")
                    STATS["sinn"]["verworfen"] = True
                    if c3["cut"] != c["cut"] and os.path.exists(c3["cut"]) and not A.keep_work:
                        os.remove(c3["cut"])
                    c3 = None
                if c3:
                    if c["cut"] != c3["cut"] and os.path.exists(c["cut"]) and not A.keep_work:
                        os.remove(c["cut"])
                    c = c3
                    QUIZ.pop(n, None)
                    wo3, wieder = reapply_fixes(c["words_out"], revs_done if rev else [])  # Regel 104
                    if wieder:
                        log(f"Nachschnitt: {len(wieder)} Untertitel-Korrekturen der Abnahme erneut eingesetzt ({'; '.join(wieder[:4])})")
                    STATS["sinn"]["korrekturen_erneut"] = wieder
                    f = burn(n, c["cut"], wo3, card_title, c["ranges"], c["kept"])
                    sc2 = sense_check(f, c)
                    if sc2:
                        STATS["sinn"].update(ok_nach=sc2["ok"], note_nach=sc2.get("note"), probleme_nach=[p["was"][:100] for p in sc2["probleme"]])
                        log(f"Sinn-Check nach Nachschnitt: {'ok' if sc2['ok'] else 'noch Hinweise'} (Note {sc2.get('note')})")
    if not A.keep_work and os.path.exists(c["cut"]):
        os.remove(c["cut"])
    words, kept, secs = c["words"], c["kept"], c["secs"]
    orig = words[-1]["e"] - words[0]["s"]
    log(f"Clip {n}: {orig:.1f} s → {secs} s, {len(c['ranges'])} Abschnitte, {len(words) - len(kept)} Wörter entfernt")
    step(0.95, "Letzter Qualitäts-Check …")
    quality_check(f, words, kept, orig, secs)
    results.append({"file": f, "title": title[:80], "seconds": secs, "songs": suggest_songs(kept, card_title),
                    "post": post_text(kept, card_title)})

if not A.keep_work:
    os.remove(wav)
if not results:
    sys.exit("Es konnten keine Clips erstellt werden.")
ki.require_or_exit("KI-Abnahme")
print(json.dumps({"clips": results}, ensure_ascii=False), flush=True)
