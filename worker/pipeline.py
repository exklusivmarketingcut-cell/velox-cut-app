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
VIRAL = A.style == "viral"
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
# Ton-Versatz (gefunden 11.10.): Bei iPhone-Aufnahmen beginnt die Tonspur 0,1–0,26 s NACH dem Bild (start_time). Ohne
# „first_pts=0“ ließ ffmpeg diesen Vorlauf in der WAV-Datei einfach weg – alle erkannten Wortzeiten lagen um genau diesen
# Betrag zu früh, der Schnitt (der mit den echten Zeitstempeln der Datei arbeitet) also auch: Wortenden abgeschnitten, vorn
# ein Rest vom gestrichenen Wort. Das war die Beschwerde „mal zu früh, mal zu spät geschnitten“. Jetzt wird der Vorlauf mit
# Stille aufgefüllt – die Zeiten der WAV-Datei sind die Zeiten der Videodatei.
run(["ffmpeg", "-v", "error", "-y", "-i", A.src, "-vn", "-ac", "1", "-ar", "16000", "-af", "aresample=async=1:first_pts=0", wav])
# Spracherkennung auf dem Original-Ton: Tests (29.09.) zeigten, dass eine künstlich angehobene Spur mehr Wörter verhört
# („Mansell“ statt „Mandeln“). Leise Stellen fängt stattdessen die Hörprobe am fertigen, ausgeglichenen Ton auf (align).
wav_asr = wav
# Ausnahme (Inhaber 08.10., Golden Thai): SEHR leise Aufnahmen (Sprache unter −33 dB). Dort fällt die Erkennung streckenweise
# in einen Notbetrieb und verhört ganze Sätze („echtes teil schafft zu essen“ statt „Echtes Thai scharf zu essen“) – der
# Sinn-Check strich den Satz daraufhin als Unsinn. Nur in diesem Fall hört die Erkennung auf einer schlicht lauter gedrehten
# Kopie (reine Verstärkung + Begrenzer, kein Filter); alle Pegel-Messungen laufen weiter auf dem Original.
try:
    import numpy as _np0
    _x0 = _np0.frombuffer(open(wav, "rb").read()[44:], dtype=_np0.int16).astype(_np0.float32) / 32768
    _lv0 = sorted(20 * _np0.log10(_np0.sqrt((_x0[i:i + 800] ** 2).mean()) + 1e-7) for i in range(0, max(0, len(_x0) - 800), 800))
    _sp0 = float(_lv0[int(len(_lv0) * 0.9)]) if _lv0 else -20.0
    if _sp0 < -33.0:
        wav_asr = os.path.join(A.out, "audio_laut.wav")
        run(["ffmpeg", "-v", "error", "-y", "-i", wav, "-af", f"volume={min(30.0, -20.0 - _sp0):.1f}dB,alimiter=limit=0.9", wav_asr])
        log(f"Sehr leise Aufnahme (Sprache {_sp0:.0f} dB) – Spracherkennung hört auf einer lauter gedrehten Kopie")
        STATS["asr_lauter_db"] = round(float(min(30.0, -20.0 - float(_sp0))), 1)
    del _x0, _lv0
except Exception as _e0:  # noqa: BLE001
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
# Wortgenaue Zuordnung (09.10.2026): Die Erkennung setzt Wortanfänge oft 0,2–0,4 s zu früh; daran hingen angeschnittene
# Wörter und zu frühe/späte Schnitte (Novus-Videos des Inhabers). Ein zweites Modell legt jedes erkannte Wort auf ~20 ms genau
# in den Ton. Übernommen wird nur, wenn das Ergebnis insgesamt zur Erkennung passt (sonst bleiben die alten Zeiten) und je
# Wort nur, wenn es nicht weiter als 1,2 s abweicht. Bis zur Abnahme nur mit VELOX_WORTGENAU=1 aktiv (Testläufe).
WG_EXTRA = []   # zusätzlich gehörte Füll-Laute (wortgenaue Zeiten) – werden beim Schnitt herausgenommen
# Seit 12.10.2026 Standard für Einzelvideos (Messlatte, 7 Novus-Rohvideos: bisheriger Schnitt im Mittel ≈ 3,7 Fehler je
# 100 Wörter, Handschnitt-Regel 1,6, meine Handschnitte 1,0). Ordner-Videos (PLAN) bleiben beim bisherigen Weg – dort füllt
# zwischen den Sätzen oft eine Handlung das Bild, die die Regel wegschneiden würde. VELOX_HANDSCHNITT=0 schaltet ab.
HAND_AN = os.environ.get("VELOX_HANDSCHNITT", "1") == "1" and not PLAN
if HAND_AN or os.environ.get("VELOX_WORTGENAU", "0") == "1":
    # Wichtig (Testläufe 09./10.10.): Die Satzauswahl (Story, Anläufe, Satzgrenzen) ist an den Wortzeiten der Erkennung geeicht.
    # Als die genauen Zeiten und die zusätzlichen „ähm“ schon dort galten, fielen Satzanfänge weg („Willst du eine“ vor
    # „Mittagspause“; „Ich würde eher erstmal gucken …“). Deshalb: Die genauen Zeiten werden hier nur AUSGERECHNET
    # (w["sa"], w["ea"]) und erst beim Schneiden eingesetzt (make_cut → wg_einsetzen). Bis dahin sieht alles die alten Zeiten.
    try:
        _ex = []
        if LANG == "de":
            # Zweites Hinhören nur auf Füll-Laute: Die Erkennung verschluckt viele „ähm“. Mit einer Vorgabe voller Füll-Laute
            # hört sie mehr davon; was im ersten Text fehlt, wird beim Schnitt als eigenes Stück herausgenommen.
            _FL = {"äh", "ähm", "ähh", "ähmm", "öh", "öhm", "ehm", "hm", "hmm"}
            _trf = mlx_whisper.transcribe(wav_asr, **ASR, hallucination_silence_threshold=2.0, temperature=0,
                                          initial_prompt="Äh... ähm... also... äh, ja, genau, ähm.")
            for _sg in _trf["segments"]:
                for _w in _sg.get("words", []):
                    _t = re.sub(r"[^\wäöüß]", "", _w["word"].lower())
                    _s, _e = float(_w["start"]), float(_w["end"])
                    if _t in _FL and not any(re.sub(r"[^\wäöüß]", "", x["t"].lower()) in _FL and abs(x["s"] - _s) < 0.8 for x in WORDS):
                        _ex.append({"t": "ähm", "s": _s, "e": max(_e, _s + 0.15), "extra": True})
        from wortgenau import wortzeiten
        _alle = sorted(WORDS + _ex, key=lambda x: x["s"])
        _wz = wortzeiten(wav, [x["t"] for x in _alle])
        if _wz:
            _dev = sorted(abs(z[0] - x["s"]) for z, x in zip(_wz, _alle) if not x.get("extra"))
            if _dev and _dev[int(len(_dev) * 0.9)] < 1.0:
                _n = 0
                for z, x in zip(_wz, _alle):
                    # Handschnitt-Regel (11.10.): Liegt die Erkennung mehr als 1,2 s daneben, steckt dazwischen fast immer etwas,
                    # das sie nicht aufgeschrieben hat (Neuanlauf „auch den, ähm, auch den Betrag“ → erkannt nur einmal, Zeiten
                    # um 1,7–2,5 s falsch). Dort ist die genaue Zuordnung die richtige – sofern sie sich ihrer Sache sicher ist.
                    if z[1] > z[0] and (abs(z[0] - x["s"]) < 1.2 or (HAND_AN
                                                                    and abs(z[0] - x["s"]) < 5.0 and z[2] >= 0.35)):
                        if x.get("extra"):
                            WG_EXTRA.append((float(z[0]), float(z[1])))
                        else:
                            x["sa"], x["ea"] = float(z[0]), float(z[1])
                            _n += 1
                STATS["wortgenau"] = {"woerter": _n, "von": len(WORDS), "abw_median": round(_dev[len(_dev) // 2], 2), "fuell_extra": len(WG_EXTRA)}
                log(f"Wortgenau: {_n} von {len(WORDS)} Wörtern neu verortet (mittlere Verschiebung {_dev[len(_dev) // 2]:.2f} s), "
                    f"{len(WG_EXTRA)} zusätzliche Füll-Laute gehört")
            else:
                log("Wortgenau: Ergebnis passt nicht zur Erkennung – alte Wortzeiten bleiben")
    except Exception as _ew:  # noqa: BLE001
        log(f"Wortgenau nicht möglich ({_ew}) – alte Wortzeiten bleiben")
# Datum/Zahlen, die die Spracherkennung zerlegt („11.“ „05.“ → „11.05.“, „19“ „:30“ → „19:30“), wieder zusammenfügen
_m = []
for w in WORDS:
    if _m and re.fullmatch(r"\d{1,2}\.?", _m[-1]["t"]) and (re.fullmatch(r"[.:]\d{2}\.?[,!?]?", w["t"]) or (
            _m[-1]["t"].endswith(".") and re.fullmatch(r"\d{1,4}\.?[,!?]?", w["t"]))) \
            and w["s"] - _m[-1]["e"] < 0.35:
        _m[-1] = {**_m[-1], "t": _m[-1]["t"] + w["t"].lstrip("."), "e": w["e"]}
    elif _m and re.fullmatch(r"\d{1,4}", _m[-1]["t"]) and re.fullmatch(r",\d{1,2}[.,!?…]*", w["t"]) and w["s"] - _m[-1]["e"] < 0.6:
        # Preise, die die Erkennung zerlegt (Testlauf 13.10., Cheesecake-Umfragen: „1“ „,99..“ → Untertitel „1 ,99..“):
        # ein Wort „1,99“, höchstens ein Satzzeichen am Ende.
        _m[-1] = {**_m[-1], "t": _m[-1]["t"] + re.sub(r"([.,!?…])[.,!?…]+$", r"\1", w["t"]), "e": w["e"]}
    elif _m and re.match(r"-\w", w["t"]) and w["s"] - _m[-1]["e"] < 0.5:
        # Bindestrich-Wörter, die die Erkennung trennt („Capri“ „-Sonne“, „Thai“ „-Küche“): im Untertitel stand sonst
        # „-SONNE“ allein (Malo's 09.10.). Sie sind EIN Wort.
        _m[-1] = {**_m[-1], "t": _m[-1]["t"] + w["t"], "e": w["e"]}
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
        # Golden Thai, 08.–09.10. (fünfmal derselbe Fehler „Mittagspause ohne Foodkoma“ ohne „Willst du eine“): Das Nachhören
        # ERFAND aus dem gedehnten Wort „Mittagspause“ ein zweites „Willst du eine Mittagspause?“ – die drei Wörter lagen in
        # der Stille davor. Danach gab es den Satzanfang doppelt, und mal wurde die echte, mal die erfundene Fassung
        # gestrichen. Nachgehörte Wörter zählen nur, wenn dort wirklich gesprochen wird, und nie, wenn sie bloß die Wörter
        # direkt davor wiederholen.
        new = [x for x in new if speech_frac(x["s"], x["e"]) >= 0.35]
        davor = [norm(x["t"]) for x in WORDS[max(0, k0 - 6):k0]]
        for L in range(min(len(new), 6), 0, -1):
            if davor[-L:] == [norm(x["t"]) for x in new[:L]] and all(davor[-L:]):
                new = new[L:]
                break
        if k1 >= k0:  # langes Wort ersetzen – nur wenn das Wort selbst wieder vorkommt (sonst eher Fehlhörer)
            # Lauf 10.10.: Aus „Mittagspause [ohne Foodkoma]“ wurde beim Nachhören „Mittagspause umgehen?“ – der Ausschnitt
            # endete mitten in den FOLGE-Wörtern, die Erkennung dichtete daraus ein Wort. Was NACH dem gesuchten Wort
            # kommt, gehört schon zu den nächsten Wörtern des Textes und wird nie eingefügt.
            ziel = norm(WORDS[k0]["t"])
            letzt = max((i_ for i_, x in enumerate(new) if norm(x["t"]) == ziel), default=None)
            if letzt is not None:
                new = new[:letzt + 1]
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
if LANG == "de":   # Messlatte 11.10.: „um“ und „eh“ sind deutsche Wörter („um das zu automatisieren“, „das ist eh klar“) – keine Füll-Laute
    FILLER -= {"um", "eh"}
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
    """Sätze am Satzzeichen – und an jeder Sprechpause ab 1,2 s. (Test 07.10., Golden Thai: Bei genuschelten Anläufen setzt die
    Erkennung keine Satzzeichen; mehrere Anläufe mit bis zu 14 s Pause dazwischen galten als EIN Satz. Davon blieb dann ein
    Stück eines kaputten Anlaufs im Video, und die saubere letzte Fassung flog raus.)"""
    out, cur = [], []
    for w in words:
        if cur and w.get("s0", w["s"]) - cur[-1].get("e0", cur[-1]["e"]) >= 1.2:   # s0/e0 = Zeiten der Erkennung (nach wg_einsetzen)
            out.append(cur)
            cur = []
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
    "gehören. Der Zuschauer darf NIE merken, dass gedreht, abgesprochen oder wiederholt wurde – das fertige Video wirkt wie aus einem Guss. "
    # Inhaber 07.10. (Schnitt-Grundsätze, an denen sich die Software messen soll): Aufmerksamkeit hält nur, wer stark einsteigt,
    # etwas Relevantes liefert und am Ende belohnt. (Der zweite Grundsatz – spätestens alle ~3 s ändert sich etwas im Bild –
    # steckt in zoom_punches, den Zwischenbildern und den Einblendungen.)
    "Jedes Video folgt dem Aufbau EINSTIEG → INHALT → AUFLÖSUNG: Die ersten zwei Sekunden müssen neugierig machen (die stärkste "
    "Frage, Behauptung oder das Ergebnis zuerst, wenn das Material es hergibt – nie mit Vorgeplänkel beginnen). Der Mittelteil "
    "liefert nur, was zum Thema gehört. Am Ende steht die Auflösung bzw. Belohnung (Antwort, Ergebnis, fertiges Produkt, Angebot, "
    "Aufruf) – die Auflösung wird NIE weggeschnitten, sonst fühlt sich der Zuschauer betrogen. ")
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
CREW_SHORT = re.compile(r"^(sorry|warte|wartet|moment|stopp?|nee warte|sekunde)\b|^(so|okay|ok|gut so)[.!?…]*$", re.I)


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
                # „wie man in fünf.“ → „wie man in fünf Minuten …“; auch mit einem Vorwort davor: „Also willst du eine Mittagspause
                # ohne Foodkoma?“ → „Willst du eine Mittagspause ohne Foodkoma, dann komm zu uns“ (Golden-Thai-Test 07.10.)
                abandoned = len(ta) < len(tb) and covered >= 0.8 * len(ta) and (ta[:2] == tb[:2] or ta[1:3] == tb[:2] or ta[:2] == tb[1:3])
                same = sm.ratio() >= 0.85  # fast wortgleich nochmal gesagt
                if abandoned or same:
                    out.update(w["i"] for k in range(a, b) for w in sents[k])
                    keep.update(w["i"] for w in sents[b])
                    break
            if b not in crew and len(tb) > 3:
                break  # dazwischen echter Inhalt → keine Wiederholung (z. B. gleiche Frage an eine andere Person)
    # Test 07.10. (Golden Thai, 4-Minuten-Monolog mit vielen Anläufen; Ergebnis bekam von der eigenen Kontrolle Note 1/10): Die
    # Regel oben sieht nur 3 Sätze / 15 s weit. Liest jemand ein Skript und sagt jeden Satz mehrmals – mit Flüchen und
    # Absprachen dazwischen –, liegen die Anläufe weit auseinander. Erkennbar daran, dass mindestens ein Drittel der Sätze
    # später fast wortgleich wiederkommt (bei einer Umfrage wiederholt sich nur die Frage). Dann gilt fürs GANZE Video:
    # von jedem Satz bleibt nur die letzte VOLLSTÄNDIGE Fassung; frühere und abgebrochene Anläufe fallen weg.
    lang = [k for k in range(len(sents)) if len(toks[k]) >= 4 and k not in crew]

    def gleich(x, y):
        sm = difflib.SequenceMatcher(None, toks[x], toks[y], autojunk=False)
        cov = sum(m.size for m in sm.get_matching_blocks())
        return sm.ratio() >= 0.72 or (cov >= 0.8 * min(len(toks[x]), len(toks[y])) and toks[x][:2] == toks[y][:2])

    # Neuansatz über die Satzgrenze: „Während alle über Avocado-Toast reden. – über Avocado-Toast reden, ignorieren 99 % …“
    # Das Ende des einen Satzes ist der Anfang des nächsten → das doppelte Stück am Ende des ersten fällt weg (immer aktiv).
    aehnlich = lambda x, y: x == y or (min(len(x), len(y)) >= 4 and difflib.SequenceMatcher(None, x, y).ratio() >= 0.8)
    for a_ in range(len(sents) - 1):
        wa = [w for w in sents[a_] if norm(w["t"]) and norm(w["t"]) not in FILLER]
        wb = [w for w in sents[a_ + 1] if norm(w["t"]) and norm(w["t"]) not in FILLER]
        for k in range(min(6, len(wa), len(wb)), 1, -1):
            if all(aehnlich(norm(wa[len(wa) - k + j]["t"]), norm(wb[j]["t"])) for j in range(k)):
                ab = sents[a_].index(wa[len(wa) - k])
                out.update(w["i"] for w in sents[a_][ab:])
                keep.update(w["i"] for w in wb[:k])
                break
    def enthalten(x, y):  # Bruchstück oder verhaspelter Anlauf desselben Satzes: der Wortschatz deckt sich (Wortfolge darf abweichen)
        if len(toks[x]) < len(toks[y]) and sum(1 for t_ in toks[x] if t_ in toks[y]) >= 0.85 * len(toks[x]):
            return True
        sx, sy = {t_ for t_ in toks[x] if len(t_) >= 3}, {t_ for t_ in toks[y] if len(t_) >= 3}
        return min(len(sx), len(sy)) >= 5 and len(sx & sy) >= 0.6 * min(len(sx), len(sy))

    spaeter = {a_: [b_ for b_ in lang if b_ > a_ and (gleich(a_, b_) or enthalten(a_, b_))] for a_ in lang}
    # Skript-Modus, wenn mindestens DREI VERSCHIEDENE Sätze später wiederkommen (Umfrage: nur die eine Frage wiederholt sich)
    koepfe = [a_ for a_ in lang if spaeter[a_] and not any(a_ in spaeter[v] for v in lang if v < a_)]
    skript_raus = set()
    if len(koepfe) >= 3:
        n_weg = 0
        for a_ in lang:
            if not spaeter[a_]:
                continue
            gruppe = [a_] + spaeter[a_]
            voll = max(len(toks[k]) for k in gruppe)
            beste = [k for k in gruppe if len(toks[k]) >= 0.7 * voll][-1]   # die LETZTE Fassung, sofern sie nicht nur ein Bruchstück ist
            if a_ != beste:
                skript_raus.update(w["i"] for w in sents[a_])  # schlägt jeden Schutz „letzte Fassung“ aus den Nah-Regeln
                n_weg += 1
            keep.update(w["i"] for w in sents[beste])
        STATS["skript_anlaeufe_entfernt"] = n_weg
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
    return (out - keep) | skript_raus, keep - skript_raus


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
                        # Die KI zieht manchmal das Nachbarwort mit hinein („Junior“ „Box“ → „Junior“ „Junior-Box“,
                        # Malo's 12.10.: Untertitel „Junior Junior-Box“). Das Nachbarwort steht aber noch da → Wort bleibt.
                        _vor = norm(chunk[i1 + d - 1]["t"]) if i1 + d > 0 else ""
                        _nach = norm(chunk[i1 + d + 1]["t"]) if i1 + d + 1 < len(chunk) else ""
                        if na != nb and ((_vor and nb == _vor + na) or (_nach and nb == na + _nach)):
                            continue
                        # Runde 4, 13.10.: „fünf Classic Cheesecakes“ wurde zu „fünf Classic Cheesecake“ – die KI glich an die
                        # Schreibweise des Kunden-Namens an und nahm das Mehrzahl-s weg. Endungen (s, n, e, en) bleiben, wie gesagt.
                        if na != nb and (na in (nb + "s", nb + "n", nb + "e", nb + "en", nb + "es") or nb in (na + "s", na + "n", na + "e", na + "en", na + "es")):
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
    # Namen & Marken des Kunden überall gleich schreiben (Testlauf 12.10., Malo's: vorn „Malo's“, hinten im selben Video
    # „Malus“ – die KI hatte nur die erste Stelle berichtigt). Fast gleich klingende Schreibweisen (gleicher Anfangsbuchstabe,
    # Länge ±1, Ähnlichkeit ab 0,8) bekommen die Schreibweise des Kunden. Nur Namen ab 5 Buchstaben; Mehrzahl-s bleibt.
    _namen = {norm(x): x for x in re.split(r"[,;\s]+", BRAND or "") if len(norm(x)) >= 5}
    for w in words:
        nw = norm(w["t"])
        if len(nw) < 4 or nw in _namen or w.get("unclear"):
            continue
        for nb, schreib in _namen.items():
            if nw[0] == nb[0] and abs(len(nw) - len(nb)) <= 1 and nw != nb + "s" and nw != nb + "n" \
                    and difflib.SequenceMatcher(a=nw, b=nb).ratio() >= 0.8:
                _sz = re.search(r"[.,!?;:…]+$", w["t"].strip())
                w["t"] = schreib.strip(".,!?;:") + (_sz.group(0) if _sz else "")
                fixed += 1
                break
    if fixed or unclear:
        log(f"Lektorat (KI): {fixed} Wörter korrigiert, {unclear} unverständliche ohne Untertitel")
    STATS.update(proofread_fixed=fixed, proofread_unclear=unclear)


gemini_proofread(WORDS)

# ── Nische des Kunden aus dem Rohmaterial (Inhaber 07.10.: „Der Kunde schreibt kein Skript und keinen Prompt. Die Software findet
# seine Nische selbst heraus und schneidet passend dazu, in dem Stil, den er gewählt hat – so, als würde ich es dir schicken.“)
# Eine kurze KI-Frage direkt nach dem Abhören: Branche, Zielgruppe, Tonfall, worum es geht. Die Antwort begleitet danach JEDE
# Entscheidung (Satzauswahl, Titel, Akzente, Musik, Schlusskontrolle) als Kontext. Ohne KI-Antwort bleibt alles wie bisher.
NISCHE, NISCHE_HINT = {}, ""


def gemini_nische(words):
    txt = " ".join(w["t"] for w in words)[:2500]
    if len(txt) < 40:
        return None
    return gemini(
        "Du bist Social-Media-Stratege. Lies das Transkript eines Rohvideos und bestimme knapp und konkret: branche (z. B. "
        "„Gastronomie – Burger-Restaurant“, „Coaching – Social-Media-Agentur“, „Handwerk – Bauunternehmen“, „Privat – Urlaubsvlog“), "
        "zielgruppe (wer soll das Video sehen), ton (z. B. locker-humorvoll, seriös-erklärend, energisch-verkaufend), "
        "inhalt (ein Satz: worum geht es in diesem Video), ziel (was soll der Zuschauer danach tun oder denken). "
        "Nur was aus dem Transkript hervorgeht – nichts erfinden." + (f" Namen/Marken des Kunden: {A.words[:200]}." if A.words else ""),
        "Transkript:\n" + txt,
        {"type": "object", "properties": {k: {"type": "string"} for k in ("branche", "zielgruppe", "ton", "inhalt", "ziel")},
         "required": ["branche", "zielgruppe", "ton", "inhalt"]})


_n = gemini_nische(WORDS)
if _n and _n.get("branche"):
    NISCHE.update({k: " ".join(str(v).split())[:160] for k, v in _n.items() if v})
    NISCHE_HINT = (f" KONTEXT DIESES KUNDEN – Branche: {NISCHE.get('branche')}. Zielgruppe: {NISCHE.get('zielgruppe')}. "
                   f"Tonfall: {NISCHE.get('ton')}. Worum es geht: {NISCHE.get('inhalt')}."
                   + (f" Ziel des Videos: {NISCHE['ziel']}." if NISCHE.get("ziel") else "")
                   + " Triff jede Entscheidung so, wie es für genau diese Branche und Zielgruppe auf TikTok/Reels am besten wirkt. ")
    CUTTER = CUTTER + NISCHE_HINT
    STATS["nische"] = dict(NISCHE)
    log(f"Nische erkannt: {NISCHE.get('branche')} · {NISCHE.get('zielgruppe')} · {NISCHE.get('ton')}")

# ── LOOK JE KUNDE (Inhaber 13.10.: „Alle Videos sehen gleich aus – das sollte auf jedes Video angepasst sein, auf das Restaurant
# oder Unternehmen.“) Mit --color auto bzw. --caption auto sucht die Software Akzentfarbe und Untertitel-Schrift selbst aus:
#   • Farbe: die kräftigen Farben, die im Material (und im Logo des Kunden) wirklich vorkommen, werden gemessen; die KI wählt
#     daraus die, die zur Marke passt – oder, wenn keine taugt, eine zur Branche. Danach wird sie auf gute Lesbarkeit gebracht.
#   • Schrift: aus den vorhandenen Untertitel-Stilen der, der zu Branche, Tonfall und Schnitt-Stil passt.
# Wer im Studio selbst Farbe oder Stil wählt, bekommt weiterhin genau das. Ohne KI-Antwort gelten die alten Standardwerte.
def bild_farben(path, n=4):
    """Die kräftigsten Farbtöne im Bild als Hex-Liste (häufigste zuerst) – Hauttöne und Holz zählen nur halb."""
    import colorsys
    import cv2 as _cv
    import numpy as _np
    px = []
    if path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        im = _cv.imread(path, _cv.IMREAD_UNCHANGED)
        if im is None:
            return []
        if im.ndim == 3 and im.shape[2] == 4:
            im = im[im[:, :, 3] > 128][:, :3].reshape(-1, 1, 3)
        px.append(_cv.resize(im, (160, max(1, min(160, im.shape[0] * 160 // max(1, im.shape[1])))))) if im.ndim == 3 and im.shape[1] > 1 else px.append(im)
    else:
        cap = _cv.VideoCapture(path)
        tot = int(cap.get(_cv.CAP_PROP_FRAME_COUNT)) or 1
        for i in range(10):
            cap.set(_cv.CAP_PROP_POS_FRAMES, int(tot * (i + 0.5) / 10))
            ok, fr = cap.read()
            if ok:
                px.append(_cv.resize(fr, (120, 213)))
        cap.release()
    if not px:
        return []
    hsv = _np.concatenate([_cv.cvtColor(p.astype(_np.uint8), _cv.COLOR_BGR2HSV).reshape(-1, 3) for p in px]).astype(_np.float32)
    h, sat, val = hsv[:, 0] * 2.0, hsv[:, 1] / 255.0, hsv[:, 2] / 255.0
    stark = (sat > 0.45) & (val > 0.35)
    if stark.sum() < 50:
        return []
    gew = (sat * val)[stark] * _np.where((h[stark] > 8) & (h[stark] < 38) & (sat[stark] < 0.75), 0.5, 1.0)
    bins = (h[stark] // 15).astype(int) % 24
    summe = _np.bincount(bins, weights=gew, minlength=24)
    out = []
    for b_ in _np.argsort(-summe)[:n]:
        if summe[b_] < 0.04 * summe.sum():
            break
        m_ = bins == b_
        r_, g_, bl_ = colorsys.hsv_to_rgb(float(_np.median(h[stark][m_])) / 360.0, float(_np.median(sat[stark][m_])), float(_np.median(val[stark][m_])))
        out.append("#%02X%02X%02X" % (int(r_ * 255), int(g_ * 255), int(bl_ * 255)))
    return out


def lesbar(hexfarbe):
    """Akzentfarbe so anheben, dass sie als Hervorhebung auf dunkler Kontur gut lesbar ist (hell und kräftig, nie grau/dunkel)."""
    import colorsys
    try:
        r_, g_, b_ = (int(hexfarbe.lstrip("#")[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except Exception:  # noqa: BLE001
        return None
    h_, s_, v_ = colorsys.rgb_to_hsv(r_, g_, b_)
    if s_ < 0.12:      # Weiß/Grau ist keine Akzentfarbe (das Wort soll sich vom weißen Text abheben)
        return None
    s_, v_ = min(0.85, max(0.5, s_)), max(0.92, v_)
    if 0.60 < h_ < 0.75:   # Blau/Violett wirkt bei voller Sättigung dunkel → etwas heller
        s_ = min(s_, 0.65)
    r_, g_, b_ = colorsys.hsv_to_rgb(h_, s_, v_)
    return "#%02X%02X%02X" % (int(r_ * 255), int(g_ * 255), int(b_ * 255))


if A.color.lower() == "auto" or A.caption.lower() == "auto":
    _stile = {"rise": "ruhig, klein geschrieben, weich – Café, Wellness, Lifestyle, Beratung",
              "clean": "klar und neutral – passt fast immer, seriös",
              "hormozi": "fette Großbuchstaben mit dicker Kontur – energisch, Verkauf, Fitness, Street Food, Burger",
              "bold": "kräftige runde Großbuchstaben – modern, jung, freundlich",
              "impact": "schmale hohe Großbuchstaben – plakativ, Angebot, Event",
              "comic": "verspielt, comicartig – Kinder, Spaß, Süßes, Humor",
              "neon": "leuchtend – Nachtleben, Bar, Club, Gaming",
              "serif": "elegante kursive Serifenschrift – gehobene Küche, Beauty, Mode, Hochzeit",
              "wide": "breite edle Großbuchstaben – Premium, Luxus, Architektur, Auto",
              "box": "Text auf dunklem Balken – erklärend, Nachrichten, Tipps", "mini": "klein und dezent – Doku, Vlog",
              "akzent": "kleine weiße Zeile, das Schlüsselwort groß und kursiv – modern und clean; gut für Gastronomie, Läden, Marken (bei Clean und Premium meist die beste Wahl)"}
    _gemessen = []
    try:
        if A.logo and os.path.exists(A.logo):
            _gemessen += [f"{c_} (aus dem Logo)" for c_ in bild_farben(A.logo, 3)]
        _gemessen += [f"{c_} (im Video)" for c_ in bild_farben(A.src, 4)]
    except Exception as _el:  # noqa: BLE001
        log(f"Farben messen nicht möglich ({_el})")
    _look = gemini(
        "Du bist Art Director für Kurzvideos von Unternehmen. Lege den Look für DIESES Video fest, damit es nach diesem Kunden "
        "aussieht und nicht wie jedes andere. (1) farbe: EINE Akzentfarbe als Hex (#RRGGBB) für hervorgehobene Wörter, Karten und "
        "Titel. Nimm bevorzugt eine der gemessenen Farben – zuerst die aus dem Logo, sonst die Farbe im Video, die erkennbar "
        "zur Marke gehört (Wandfarbe, Schild, Verpackung, Kleidung des Teams) – nicht Hautton oder Holz. Passt keine, wähle eine, die "
        "zur Branche und zum Namen passt. Sie muss kräftig und hell sein. (2) untertitel: genau einer dieser Stile, passend zu "
        "Branche, Tonfall und Schnitt-Stil: " + "; ".join(f"{k_} = {v_}" for k_, v_ in _stile.items()) + ". (3) grund: ein Satz.",
        f"Schnitt-Stil: {A.style}. Namen/Marken: {A.words[:160] or '–'}. Branche: {NISCHE.get('branche', '?')}. Zielgruppe: "
        f"{NISCHE.get('zielgruppe', '?')}. Tonfall: {NISCHE.get('ton', '?')}. Worum es geht: {NISCHE.get('inhalt', '?')}.\n"
        f"Gemessene Farben: {', '.join(_gemessen) or 'keine kräftige Farbe gefunden'}",
        {"type": "object", "properties": {"farbe": {"type": "string"}, "untertitel": {"type": "string"}, "grund": {"type": "string"}},
         "required": ["farbe", "untertitel"]}) or {}
    _f = lesbar(str(_look.get("farbe") or "")) if re.fullmatch(r"#?[0-9a-fA-F]{6}", str(_look.get("farbe") or "").strip()) else None
    if A.color.lower() == "auto":
        # Test 13.10. (Cheesecake): Die KI wählte Blau als „frischen Kontrast“ – gemessen war vor allem das Gelb des Ladens. Die
        # Akzentfarbe soll nach dem KUNDEN aussehen: Gibt es gemessene Farben, muss die Wahl im Farbton nah an einer der beiden
        # häufigsten liegen (Logo zuerst); sonst gilt die häufigste gemessene.
        import colorsys as _cs
        _ton = lambda hx: _cs.rgb_to_hsv(*(int(hx.lstrip("#")[i_:i_ + 2], 16) / 255.0 for i_ in (0, 2, 4)))[0]
        _mess = [m_.split()[0] for m_ in _gemessen[:2]]
        if _f and _mess and min(min(abs(_ton(_f) - _ton(m_)), 1 - abs(_ton(_f) - _ton(m_))) for m_ in _mess) > 0.07:
            log(f"Look: gewählte Farbe {_f} gehört nicht zu den Farben des Kunden {_mess} – nehme die häufigste gemessene")
            _f = lesbar(_mess[0])
        A.color = _f or (lesbar(_gemessen[0].split()[0]) if _gemessen else None) or "#F6E27F"
    if A.caption.lower() == "auto":
        _u = str(_look.get("untertitel") or "").strip().lower()
        A.caption = _u if _u in _stile else {"clean": "clean", "viral": "hormozi", "premium": "wide"}[A.style]
    STATS["look"] = {"farbe": A.color, "untertitel": A.caption, "gemessen": _gemessen, "grund": str(_look.get("grund") or "")[:160]}
    log(f"Look für diesen Kunden: Akzentfarbe {A.color}, Untertitel „{A.caption}“ – {str(_look.get('grund') or 'Standard')[:140]}")

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


_PAN_REDET = {}


def keep_ranges(words, safe=False, extra_drop=frozenset(), extra_keep=frozenset()):
    """Zeitbereiche, die bleiben: ohne Füllwörter/Wiederholungen, Stille raus, Schnitte nur zwischen Wörtern."""
    fil, rep, gem = filler_idx(words), repeat_idx(words), gemini_cuts(words)
    ret, final = retake_idx(words)
    gem = set(gem)
    if PLAN:
        # Testlauf 13.10. (Malo's): Die KI strich einmal den ganzen Satz „Und jetzt ist unser Junior Burger fertig.“ – in anderen
        # Läufen blieb er drin. Bei Ordner-Videos hat der Schnittplan jeden Satz bewusst ausgewählt; beim Feinschnitt darf die KI
        # deshalb keinen GANZEN Satz (ab 4 Wörtern) mehr streichen – nur Teile davon. Ausnahme: der Satz kommt doppelt vor.
        _saetze, _cur = [], []
        for w in words:
            _cur.append(w)
            if SENT_END.search(w["t"]):
                _saetze.append(_cur)
                _cur = []
        if _cur:
            _saetze.append(_cur)
        _inh = lambda st: [w for w in st if norm(w["t"]) and norm(w["t"]) not in FILLER]
        _txt = [[norm(w["t"]) for w in _inh(st)] for st in _saetze]
        for st, tx in zip(_saetze, _txt):
            if len(tx) >= 4 and all(w["i"] in gem for w in _inh(st)) and not any(w["i"] in (rep | ret) for w in st) and _txt.count(tx) < 2:
                gem -= {w["i"] for w in st if w["i"] not in fil}
                log(f"KI-Streichung zurückgenommen – ganzer geplanter Satz bleibt: „{' '.join(w['t'] for w in st)[:70]}“")
                STATS["satz_gerettet"] = STATS.get("satz_gerettet", 0) + 1
    drop = (fil | rep | gem | ret | set(extra_drop)) - (final - fil)  # letzte Fassung einer Wiederholung bleibt immer
    # Kontroll-Lauf 08.10. abends: In „Willst du eine Mittagspause ohne Foodkoma“ strich die KI einmal nur das „ohne“ – der Satz
    # war kaputt. Ein EINZELNES Wort mitten zwischen zwei behaltenen Wörtern darf die KI allein nur streichen, wenn es ein
    # Füll-/Einwurfwort oder die Doppelung eines Nachbarn ist; alles andere bleibt (Inhaltswörter fallen nie einzeln weg).
    _nn = lambda w: re.sub(r"[^a-zäöüß0-9]", "", w["t"].lower())
    _einwurf = {"ja", "so", "okay", "ok", "also", "äh", "ähm", "hm", "genau", "ne", "nein", "halt", "und", "na", "gut", "ey", "boah", "ach"}
    geholt_ki = 0
    for k in range(1, len(words) - 1):
        w = words[k]
        if w["i"] in gem and w["i"] in drop and w["i"] not in (fil | rep | ret | set(extra_drop)) \
                and words[k - 1]["i"] not in drop and words[k + 1]["i"] not in drop \
                and _nn(w) and _nn(w) not in _einwurf and _nn(w) not in (_nn(words[k - 1]), _nn(words[k + 1])) \
                and words[k + 1]["s"] - words[k - 1]["e"] < 1.2:
            drop.discard(w["i"])
            geholt_ki += 1
    if geholt_ki:
        STATS["einzelwort_der_ki_behalten"] = geholt_ki
    # Inhaber 08.10. nachts (Viral, „nach ‚zu essen‘ nicht richtig geschnitten“): Vor „wenn du es ohne Milch schaffst“ stand ein
    # einzelnes „Okay,“ – 0,9 s nach dem Satz davor, 0,8 s vor dem nächsten Wort. Das ist ein Startzeichen an sich selbst oder
    # den Kameramann, kein Inhalt: Ein einzelnes Einwurfwort, das frei zwischen zwei Pausen steht, fällt weg.
    _start = {"okay", "ok", "so", "gut", "also", "genau", "ja", "und", "los", "bitte"}
    frei = 0
    for k, w in enumerate(words):
        if w["i"] in drop or _nn(w) not in _start:
            continue
        davor = w["s"] - words[k - 1]["e"] if k else 9.0
        danach = words[k + 1]["s"] - w["e"] if k + 1 < len(words) else 9.0
        if davor >= 0.5 and danach >= 0.5:
            drop.add(w["i"])
            frei += 1
    if frei:
        STATS["startzeichen_gestrichen"] = frei
    # nachvollziehbar machen, WER ein Wort gestrichen hat (Fehlersuche 08.10.: „ohne“ fehlte in „Mittagspause ohne Foodkoma“)
    STATS["gestrichen_von"] = {f"{w['t']}@{w['s']:.1f}": "+".join(n for n, m in (("fuell", fil), ("wdh", rep), ("ki", gem), ("anlauf", ret), ("extra", set(extra_drop))) if w["i"] in m)
                               for w in words if w["i"] in drop}
    # Regel 116 (Kontroll-Lauf 08.10.): „Willst du eine – Willst du eine Mittagspause ohne Foodkoma“. Die KI strich einmal den
    # Anlauf (richtig), im nächsten Lauf die ZWEITE Fassung samt „Mittagspause ohne“ – übrig blieb „Willst du eine Foodkoma“.
    # Fest verdrahtet: Stehen dieselben 2–6 Wörter direkt hintereinander zweimal da und die spätere Fassung ist gestrichen,
    # wird getauscht – der Anlauf fällt weg, die spätere Fassung bleibt samt den mit ihr gestrichenen Folgewörtern.
    _n = lambda w: re.sub(r"[^a-zäöüß0-9]", "", w["t"].lower())
    k, getauscht = 0, 0
    while k < len(words):
        hit = None
        for L in range(6, 1, -1):
            A_ = words[k:k + L]
            if len(A_) < L or any(w["i"] in drop for w in A_) or not all(_n(w) for w in A_):
                continue
            j = k + L
            while j < len(words) and words[j]["i"] in drop and [_n(w) for w in words[j:j + L]] != [_n(w) for w in A_]:
                j += 1
            B_ = words[j:j + L]
            if len(B_) == L and [_n(w) for w in B_] == [_n(w) for w in A_] and all(w["i"] in drop for w in B_) \
                    and B_[0]["s"] - A_[-1]["e"] < 6.0:
                hit = (L, j)
                break
        if not hit:
            k += 1
            continue
        L, j = hit
        m = j + L
        while m < len(words) and words[m]["i"] in drop and words[m]["i"] not in fil and m - (j + L) < 4:
            m += 1
        drop |= {w["i"] for w in words[k:k + L]}
        drop -= {w["i"] for w in words[j:m] if w["i"] not in fil}
        getauscht += 1
        k = m
    # Inhaber 08.10. (Viral, „am Location-Wechsel ganz am Anfang ein Versprecher“): „Halal Thai? Nein. Halal Thai ist kein
    # Trend.“ – Anlauf, Selbstkorrektur, neuer Anlauf. Steht zwischen zwei gleichen Anfängen (2–6 Wörter) nur ein
    # Korrekturwort, fallen der erste Anlauf und das Korrekturwort weg.
    _korr = {"nein", "ne", "nee", "quatsch", "nochmal", "sorry", "stopp", "stop", "moment", "falsch", "warte", "anders", "also", "äh", "ähm"}
    k = 0
    while k < len(words):
        ok_ = False
        for L in range(6, 1, -1):
            A_ = words[k:k + L]
            if len(A_) < L or any(w["i"] in drop for w in A_) or not all(_n(w) for w in A_):
                continue
            j = k + L
            mitte = []
            while j < len(words) and len(mitte) < 3 and (words[j]["i"] in drop or not _n(words[j]) or _n(words[j]) in _korr):
                if words[j]["i"] not in drop and _n(words[j]) in _korr:
                    mitte.append(j)
                j += 1
            B_ = words[j:j + L]
            if mitte and len(B_) == L and [_n(w) for w in B_] == [_n(w) for w in A_] and not any(w["i"] in drop for w in B_):
                drop |= {w["i"] for w in words[k:j]}
                getauscht += 1
                k, ok_ = j, True
                break
        if not ok_:
            k += 1
    # Kontroll-Lauf 09.10. („Tagspause ohne Foodkoma“): Beide Fassungen von „Willst du eine“ waren gestrichen – die erste als
    # Wiederholung, die zweite von der KI. Steht ein Satzanfang (2–6 Wörter) zweimal direkt hintereinander da, bleibt IMMER
    # genau eine Fassung: sind beide gestrichen und geht es danach mit einem behaltenen Wort weiter, kommt die spätere zurück.
    k = 0
    while k < len(words):
        ok_ = False
        for L in range(6, 1, -1):
            A_ = words[k:k + L]
            if len(A_) < L or not all(w["i"] in drop for w in A_) or not all(_n(w) for w in A_):
                continue
            j = k + L
            while j < len(words) and words[j]["i"] in drop and [_n(w) for w in words[j:j + L]] != [_n(w) for w in A_]:
                j += 1
            B_ = words[j:j + L]
            if len(B_) == L and [_n(w) for w in B_] == [_n(w) for w in A_] and all(w["i"] in drop for w in B_) \
                    and j + L < len(words) and words[j + L]["i"] not in drop and B_[0]["s"] - A_[-1]["e"] < 6.0 \
                    and not any(w["i"] in fil for w in B_):
                drop -= {w["i"] for w in B_}
                getauscht += 1
                k, ok_ = j + L, True
                break
        if not ok_:
            k += 1
    if getauscht:
        STATS["letzte_fassung_getauscht"] = getauscht
    # Zweiter Kontroll-Lauf 08.10.: „Halal Thai … Halal Thai ist kein Trend“ – gestrichen wurden der Anlauf UND das zweite
    # „Halal“; zu hören war „Thai ist kein Trend“. Die letzte Fassung beginnt dort, wo der Anlauf begann: Steht direkt vor dem
    # ersten behaltenen Wort ein gestrichenes Wort, das mit ihm zusammen schon im gestrichenen Anlauf davor vorkommt, bleibt es.
    geholt = 0
    for _ in range(3):
        for m in range(2, len(words)):
            if words[m]["i"] in drop or words[m - 1]["i"] not in drop or words[m - 1]["i"] in fil or not _n(words[m - 1]):
                continue
            q = m - 2
            while q >= 0 and words[q]["i"] in drop and m - q <= 8:
                if _n(words[q]) == _n(words[m - 1]) and q + 1 < m - 1 and _n(words[q + 1]) == _n(words[m]):
                    drop.discard(words[m - 1]["i"])
                    geholt += 1
                    break
                q -= 1
    if geholt:
        STATS["fassungsanfang_zurueckgeholt"] = geholt
    # Regel 114: stumme Zeige-Strecken der Montage (Arbeitsschritte als schnelle Bildfolge). Dort gibt es keine Sprache –
    # was die Erkennung trotzdem „hört“ (Halluzination auf Stille), wird nie Untertitel.
    zeigen = [(float(a), float(b)) for a, b in ((PLAN or {}).get("zeigen") or []) if b - a > 0.2]
    drop |= {w["i"] for w in words if any(a - 0.05 <= w["s"] < b for a, b in zeigen)}
    # Regel 96: Was WÄHREND eines Kameraschwenks nebenbei gesagt wird („lassen es kurz hierhin“), fliegt mit dem Schwenk raus –
    # außer den letzten Wörtern, mit denen der nächste Satz schon beginnt („jetzt zwei Gurken“)
    pan_cuts = []
    for pa, pb in pans():  # auch im sicheren Modus
        inside = [w for w in words if pa <= w["s"] < pb]
        # Messlatte 12.10. (Novus „Problem“, im Auto aus der Hand gefilmt): Die Regel hielt das Wackeln für Kameraschwenks und
        # strich, was dabei gesagt wurde – „dass sie keine Kunden haben“ wurde zu „dass sie kommen“. Die Regel ist für
        # Zubereitungs-/Produktbilder gedacht (Kamera schwenkt zur Beilage). Spricht dabei jemand IN die Kamera (Gesicht im
        # Bild) oder wird durchgehend geredet (≥ 4 Wörter, über 2,2 Wörter/s), ist es kein Schwenk zum Wegschneiden.
        _gesp = [w for w in inside if w["i"] not in drop]
        if _gesp:
            _key = (round(pa, 1), round(pb, 1))
            if _key not in _PAN_REDET:
                try:
                    _PAN_REDET[_key] = bool(face_info(pa, pb)) or (len(_gesp) >= 4 and len(_gesp) / max(0.5, pb - pa) > 2.2)
                except Exception:  # noqa: BLE001
                    _PAN_REDET[_key] = len(_gesp) >= 4
            if _PAN_REDET[_key]:
                continue
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
    # Übrig gebliebenes Einzelwort (Runde 5, 13.10.): „Das Spielzeug ist immer anders, nicht [immer das Gleiche.]“ und
    # „Ich [höre, ja.]“ – der Rest des Satzteils war gestrichen, ein einzelnes Wort blieb stehen und hing in der Luft
    # (hörbar und im Untertitel). Bleibt von einem Satzteil (bis zum nächsten Komma/Punkt, mind. 3 Wörter) nur ein einziges
    # Wort übrig und ist das ENDE des Satzteils gestrichen, kann er nicht für sich stehen – das Wort fällt mit weg.
    _teil, _teile = [], []
    for w in words:
        _teil.append(w)
        if re.search(r"[,.!?;:…]\W*$", w["t"].strip()):
            _teile.append(_teil)
            _teil = []
    if _teil:
        _teile.append(_teil)
    for tl_ in _teile:
        inh_ = [w for w in tl_ if norm(w["t"])]
        rest_ = [w for w in inh_ if w["i"] not in drop]
        if len(inh_) >= 3 and len(rest_) == 1 and inh_[-1]["i"] in drop and rest_[0]["i"] not in set(extra_keep):
            drop.add(rest_[0]["i"])
            STATS["einzelwort_raus"] = STATS.get("einzelwort_raus", []) + [rest_[0]["t"]]
            log(f"Einzelwort ohne seinen Satzteil entfernt: „{rest_[0]['t']}“ (aus „{' '.join(w['t'] for w in tl_)[:60]}“)")
    # ABGESCHNITTENER SCHLUSS (Inhaber 13.10.: „das Video endet zu früh“ – Golden Thai Veggie hörte mitten im Namen auf:
    # „… unsere Nummer 33, das Pad Pak Ruam“, gesagt wurde „… Pad Pak Ruam Mit, mit Tofu.“; die Regel gegen Doppelungen hatte
    # das Satzende gestrichen). Der letzte Satz des Videos läuft immer bis zu SEINEM Satzende: Ist das letzte behaltene Wort
    # kein Satzende und sind die Wörter bis zum Satzende (höchstens 6, höchstens 3,5 s) gestrichen, kommen sie zurück.
    _beh = [k_ for k_, w in enumerate(words) if w["i"] not in drop and norm(w["t"])]
    if _beh and not SENT_END.search(words[_beh[-1]]["t"].strip()):
        _l, _rueck = _beh[-1], []
        for w in words[_l + 1:_l + 7]:
            if w["s"] - words[_l]["e"] > 3.5:
                break
            _rueck.append(w)
            if SENT_END.search(w["t"].strip()):
                break
        if _rueck and SENT_END.search(_rueck[-1]["t"].strip()) and not any(a_ - 0.05 <= w["s"] < b_ for w in _rueck for a_, b_ in zeigen):
            drop -= {w["i"] for w in _rueck if w["i"] not in fil}
            STATS["schluss_ergaenzt"] = " ".join(w["t"] for w in _rueck)
            log(f"Schluss vervollständigt – der letzte Satz läuft bis zu seinem Ende: „… {words[_l]['t']} {' '.join(w['t'] for w in _rueck)}“")
    drop -= set(extra_keep)   # Lesekontrolle: einzelne nötige Wörter (Subjekt, Artikel) kommen zurück
    STATS.update(filler=len(fil), repeats=len(rep), gemini=len(gem), retakes=len(ret), gemini_on=bool(GEMINI_KEY),
                 removed=[w["t"] for w in words if w["i"] in drop][:80])
    kept = [w for w in words if w["i"] not in drop]
    PAD_IN, PAD_OUT, MAX_GAP = (0.08, 0.2, 0.2) if safe else (0.05, 0.1, 0.14)
    ranges = []
    _pos = {w["i"]: k for k, w in enumerate(words)}
    for w in kept:
        tail = PAD_OUT + (0.08 if SENT_END.search(w["t"]) or w["t"].endswith(",") else 0)  # Satzende darf ausklingen
        if w is kept[-1]:  # letztes Wort des Videos: Whisper setzt das Ende oft zu früh („Capri-Son-“) – lieber kurz ausklingen
            tail = max(tail, 0.45)
        s, e = max(0.0, w["s"] - PAD_IN), min(DUR, w["e"] + tail)
        if "s0" in w:   # wortgenau verortet: der Rand darf nie in ein gestrichenes Nachbarwort hineinreichen (sonst bleibt z. B.
            _k = _pos.get(w["i"], -1)   # im vorsichtigen Modus mit seinen breiten Rändern ein Stück „ähm“ stehen – Lauf p2_fehler 09.10.)
            if _k > 0 and words[_k - 1]["i"] in drop and "s0" in words[_k - 1]:
                s = max(s, min(words[_k - 1]["e"], w["s"]))
            if 0 <= _k < len(words) - 1 and words[_k + 1]["i"] in drop and "s0" in words[_k + 1]:
                e = min(e, max(words[_k + 1]["s"], w["e"]))
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

# DaVinci Resolve als Farb-Stufe (seit 12.10.2026, Inhaber: „die Software mit DaVinci verbinden“). Nur mit VELOX_RESOLVE=1
# und nur, wenn Resolve offen ist und die Brücke läuft (Workspace → Scripts → resolve_mcp_bridge). Dann rechnet der Schnitt
# OHNE die ffmpeg-Farbe, und der fertige Schnitt läuft durch Resolve (gemessene Grundkorrektur: Schwarz-/Weißpunkt,
# Weißabgleich, Mitten, Sättigung – worker/resolve.py). Geht dabei irgendetwas schief, bekommt der Schnitt nachträglich die
# gewohnte ffmpeg-Farbe: Ein Auftrag scheitert nie an Resolve.
RESOLVE, GRADE_FF = None, GRADE
if os.environ.get("VELOX_RESOLVE", "0") == "1":
    try:
        import resolve as _rs
        if _rs.rate(min(FPS, 60.0)) and _rs.alive():
            RESOLVE = _rs
            GRADE = "vignette=angle=PI/5,noise=alls=3:allf=t" if PREM else "null"
        else:
            log("Resolve nicht erreichbar oder Bildrate unpassend → Farbe wie gewohnt mit ffmpeg")
    except Exception as _e:
        log(f"Resolve-Anschluss: {_e} → Farbe wie gewohnt mit ffmpeg")
    STATS["resolve"] = {"an": bool(RESOLVE)}


RESOLVE_OFFEN = {}  # Schnittdatei → Aufnahmen-Liste: wartet noch auf die Farbe aus Resolve


def resolve_farbe(cut, shots=None):
    """Fertigen Schnitt durch Resolve schicken (Farbe). Scheitert das, die gewohnte ffmpeg-Farbe nachholen."""
    tmp = cut[:-4] + ".farbe.mp4"
    t0 = __import__("time").time()
    if A.keep_work:  # Testläufe: den Schnitt ohne Farbe aufheben (Farb-Abstimmung ohne neuen Schnitt)
        __import__("shutil").copyfile(cut, cut[:-4] + ".roh.mp4")
    try:
        cdl = RESOLVE.grade(cut, tmp, A.style, shots=shots if shots and len(shots) > 1 else None)
        os.replace(tmp, cut)
        STATS["resolve"].update(ok=True, sek=round(__import__("time").time() - t0, 1), cdl=cdl)
        if "aufnahmen" in cdl:
            log(f"Resolve-Farbe: {cdl['shots']} Stücke, {cdl['gruppen']} verschiedene Aufnahmen – " + "; ".join(
                f"{k}: {v['Slope'].split()[1]}/{v['Power'].split()[0]}/{v['Saturation']}" for k, v in cdl["aufnahmen"].items()))
        else:
            log(f"Resolve-Farbe: {cdl['Slope']} | {cdl['Offset']} | {cdl['Power']} | Sättigung {cdl['Saturation']}")
    except Exception as e:
        log(f"Resolve-Farbe fehlgeschlagen ({e}) → ffmpeg-Farbe")
        STATS["resolve"].update(ok=False, fehler=str(e)[:160])
        run(["ffmpeg", "-v", "error", "-y", "-i", cut, "-vf", GRADE_FF.split(",vignette")[0], "-c:v", "libx264", "-preset",
             "veryfast", "-crf", "16", "-c:a", "copy", tmp])
        os.replace(tmp, cut)


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
        if viral and not PREM:
            # Inhaber 10.10.: „Mal zoomt er rein, mal raus, dann zweimal kurz rein – ohne Verständnis.“ Bisher wechselte die Stufe
            # an JEDEM Schnitt reihum durch vier Stufen. Jetzt hat der Zoom einen Grund: Die Stufe wechselt nur, wenn ein neuer
            # Satz beginnt (neuer Gedanke = neue Einstellung: normal ↔ nah), frühestens nach 2 s. Schnitte MITTEN im Satz
            # bleiben in derselben Einstellung; nur bei einem größeren Zeitsprung rückt das Bild ein kleines Stück (3 %),
            # damit der Schnitt gewollt aussieht.
            if k == 0:
                _zl, _zs, _zt = 0, 0, 0.0
            else:
                _wp = next((w_ for w_ in reversed(WORDS) if w_["s"] < ranges[k - 1][1]), None)
                if _wp and SENT_END.search(_wp["t"]) and t_a - _zt >= 2.0:
                    _zl, _zs, _zt = 1 - _zl, 0, t_a
                elif gap >= 0.4:
                    _zs = 1 - _zs
            zoom = ((1.0, 1.10)[_zl] + 0.03 * _zs) if closeup else (1.0, 1.05)[_zl]
        elif viral:
            # Premium (alter Weg): unverändert – der Inhaber ist mit den Premium-Zooms zufrieden
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
    # „Akzent“ (13.10., nach dem von Hand geschnittenen Eröffnungs-Video und dem Stil des Inhabers: „clean, kleine Untertitel,
    # typografische Hervorhebungen“): kleine weiße Zeile, Wort für Wort – das Schlüsselwort am Ende eines Satzteils steht kurz
    # allein, groß und kursiv („London,“ · „Cheesecakes,“ · „geöffnet.“).
    "akzent":  dict(font="Poppins SemiBold", size=92, bold=0, ital=0, bord=1, shad=4, back="&H78000000", n=4, chars=18, case=None, pop=False, blur=0, gross=1.95, weiss=True, file="Poppins-normal-600.ttf", k=0.564),
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
    _lg = [-9.0]   # Stil „Akzent“: wann zuletzt ein Wort groß stand (höchstens alle 1,8 s eines)
    for gi, g in enumerate(groups):
        if any(a <= w[1] < b for w in g for a, b in hide):
            continue  # großes Schlüsselwort/Zähler steht gerade im Bild → diese Untertitel-Zeile ganz weglassen (nie halbe Zeilen)
        _gb = None
        if CS.get("gross") and len(norm(g[-1][0])) >= 6 and re.search(r"[.,!?]\W*$", g[-1][0].strip()) and g[-1][1] - _lg[0] >= 1.8:
            wb_ = g[-1]
            _lg[0] = wb_[1]
            nx_ = groups[gi + 1][0][1] if gi + 1 < len(groups) else None
            eb_ = nx_ if nx_ is not None and nx_ - wb_[2] < 0.7 else wb_[2] + 0.35
            tb_ = wb_[0].strip().replace("{", "(").replace("}", ")")
            fb_ = int(fs0 * CS["gross"])
            while fb_ > fs0 and cap_w(tb_, fb_) * 1.12 > CAP_MAXW:
                fb_ -= 6
            ev.append(f"Dialogue: 1,{ass_time(wb_[1])},{ass_time(eb_)},Cap,,0,0,0,,{{\\an5\\pos(540,{y})\\fnPoppins ExtraBold\\fs{fb_}\\i1\\bord1.5\\shad5"
                      f"\\fad(50,60)\\fscx92\\fscy92\\t(0,110,\\fscx103\\fscy103)\\t(110,200,\\fscx100\\fscy100)}}{tb_}")
            _gb = wb_[1]
            g = g[:-1]
            if not g:
                continue
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
        g_end = _gb if _gb is not None else (nxt if nxt is not None and nxt - g[-1][2] < 0.7 else g[-1][2] + 0.3)
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
                if j == k and PREM:
                    # Premium-Untertitel (Inhaber 07.10.: „Premium muss sich deutlich abheben – auch wie die Untertitel
                    # eingeblendet werden“): Das gerade gesprochene Wort bekommt eine Marke in der Akzentfarbe, die kurz
                    # aufpoppt; das Wort selbst steht dunkel darauf. Clean/Viral färben nur das Wort.
                    bw_ = cap_w(t, fs) + fs * 0.22
                    bh_ = fs * CS["k"] / (CS.get("sx", 100) / 100) * 1.42
                    r_ = min(bh_ * 0.28, 22)
                    form = (f"m {r_:.0f} 0 l {bw_ - r_:.0f} 0 b {bw_:.0f} 0 {bw_:.0f} 0 {bw_:.0f} {r_:.0f} l {bw_:.0f} {bh_ - r_:.0f} "
                            f"b {bw_:.0f} {bh_:.0f} {bw_:.0f} {bh_:.0f} {bw_ - r_:.0f} {bh_:.0f} l {r_:.0f} {bh_:.0f} "
                            f"b 0 {bh_:.0f} 0 {bh_:.0f} 0 {bh_ - r_:.0f} l 0 {r_:.0f} b 0 0 0 0 {r_:.0f} 0")
                    ev.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e_show)},Cap,,0,0,0,,{{\\an5\\pos({xs[j]:.1f},{y})\\bord0\\shad3\\1c{ACC}"
                              f"\\fscx80\\fscy80\\t(0,90,\\fscx100\\fscy100)\\p1}}{form}{{\\p0}}")
                    tag += "\\1c&H00161412&\\bord0\\shad0"
                elif j == k and not CS.get("weiss"):
                    tag += "\\1c" + ACC
                    if pop:  # nur dieses Wort wächst minimal – um seine eigene Mitte, die Nachbarn bleiben stehen
                        tag += "\\t(0,90,\\fscx109\\fscy109)\\t(90,170,\\fscx105\\fscy105)" if not appear else \
                               "\\t(110,180,\\fscx105\\fscy105)"
                elif j > k and dim:  # kommt gleich – schon sichtbar, leicht gedimmt
                    tag += "\\alpha&H55&"
                elif j < k and is_emph(g[j][0]):
                    # Inhaber 10.10. („Clean ist zu wenig“): Zahlen, Preise und Namen bleiben nach dem Sprechen farbig – in
                    # JEDER Untertitel-Schrift (vorher nur bei vier von zehn). Kostet nichts an Ruhe, hebt aber das Wichtige.
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
    if not PREM:
        # Inhaber 10.10.: Die zusätzlichen Zooms „alle 3 s“ machten zusammen mit den Schnitt-Zooms den Durchmix. Bei Viral
        # zoomt das Bild jetzt nur noch kurz rein, wenn es etwas zu betonen gibt (Pointe, Zahl, Produkt) – mit mindestens
        # 2,5 s Abstand. Für Bewegung dazwischen sorgt der Wechsel der Einstellung am Satzanfang (plan_crops).
        _p2 = []
        for t in pts:
            if not _p2 or t - _p2[-1] >= 2.5:
                _p2.append(t)
        pts = _p2
        if not any(t < 5.0 for t in pts):
            first = next((times[w["i"]] for w in kept_ws if 1.0 <= times[w["i"]] <= 4.5 and is_emph(w["t"])), None)
            if first is not None and all(abs(first - p) >= 2.5 for p in pts):
                pts.insert(0, first)
        return sorted(pts)
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
    # Inhaber 08.10.: „Kannst gerne das Broadcast benutzen und so optimieren, dass man die Stimmen besser hört und die Geräusche
    # außenherum leicht unterdrückt werden.“ Broadcast = sein Premiere-Preset (Multiband-Kompressor, material/preset/Broadcast
    # Preset.prfpset), Werte 1:1 ausgelesen: Trennfrequenzen 137/1147/6910 Hz, Schwellen −20,2/−17,6/−24,8/−25 dB, Verhältnis
    # 3:1/2:1/2:1/3:1, Verstärkung +1/0/0/+3 dB, Attack 1 ms, Release 100 ms, Begrenzer mit +10 dB Eingang.
    # Davor: die Sprache auf einen festen Arbeitspegel bringen (sonst greift das Preset bei leisen Aufnahmen gar nicht) und
    # zwischen den Wörtern sanft absenken (Expander, höchstens −9 dB – KEINE Entrauschung, die klang „wie unter Wasser“).
    # Hörvergleich 08.10. am Golden-Thai-Rohschnitt: Stimme gleich laut, Umgebung zwischen den Wörtern 7 dB leiser.
    _lvh = sorted(v for v in LV if v > -95.0) or sorted(LV)   # ohne digitale Stille (stumme Bildfolge)
    _laut = _lvh[int(len(_lvh) * 0.9)] if _lvh else -24.0
    vor_db = min(34.0, max(-6.0, -20.0 - _laut))
    fc.append(f"[{prev}]{tempo}highpass=f=80:p=2,equalizer=f=220:t=q:w=1.2:g=-0.8,equalizer=f=3000:t=q:w=1.2:g=1.5,"
              # Inhaber 08.10. nachts nach dem Hörvergleich an der Straßen-Umfrage: „Broadcast + Rauschen leicht und nur
              # Broadcast sind ganz in Ordnung“ → die leichte Entrauschung (6 dB, lernt das Grundrauschen mit) bleibt drin.
              f"volume={vor_db:.1f}dB,afftdn=nr=6:nf=-45:tn=1,"
              f"acrossover=split=137 1147 6910:order=4th[bq1][bq2][bq3][bq4]")
    fc.append("[bq1]acompressor=threshold=0.0977:ratio=3:attack=1:release=100:makeup=1.122[bc1]")
    fc.append("[bq2]acompressor=threshold=0.132:ratio=2:attack=1:release=100:makeup=1[bc2]")
    fc.append("[bq3]acompressor=threshold=0.0575:ratio=2:attack=1:release=100:makeup=1[bc3]")
    fc.append("[bq4]acompressor=threshold=0.0562:ratio=3:attack=1:release=100:makeup=1.413[bc4]")
    fc.append(f"[bc1][bc2][bc3][bc4]amix=inputs=4:normalize=0,volume=10dB,alimiter=limit=0.977:attack=5:release=50:level=0,"
              # zweite, sanfte Absenkung NACH dem Kompressor (der hebt die Umgebung sonst wieder an – Straßen-Umfrage 08.10.:
              # nur davor +1,5 dB Abstand Stimme/Umgebung, davor und danach +5 dB)
              # Inhaber 08.10. nachts zu den Hörproben mit Absenkung zwischen den Wörtern (Expander, leicht und stark):
              # „abgehackt“. Deshalb KEIN Expander/Gate mehr – die Umgebung darf nicht auf- und zugehen.
              f"loudnorm=I=-14:TP=-1.5:LRA=11,"
              # Inhaber 09.10.: „Der Ton kommt ein paar Millisekunden hinterher.“ Gemessen mit Testsignal: Entrauschung 25 ms +
              # Begrenzer 5 ms = 30 ms Verzögerung (die alte Kette hatte 0). Genau diese 30 ms werden vorn abgeschnitten.
              f"atrim=start=0.030,asetpts=PTS-STARTPTS,"
              f"afade=t=in:d=0.03,afade=t=out:st={max(0.0, total - 0.18):.3f}:d=0.18,aresample=48000[ac]")
    STATS["ton"] = {"kette": "broadcast", "vorverstaerkung_db": round(vor_db, 1)}
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
    if RESOLVE:
        # Video aus mehreren Rohclips: Wo im fertigen Schnitt wechselt die Aufnahme? (Clip-Grenzen aus dem Schnittplan.)
        # Jede Aufnahme bekommt in Resolve ihre eigene Korrektur – hell/dunkel, warm/kalt gedrehte Clips passen dann zusammen.
        shots = None
        if PLAN and PLAN.get("joins"):
            import bisect
            _js, _a, shots = sorted(PLAN["joins"]), 0, []
            for (s_, e_), f_ in zip(ranges, nf):
                k_ = bisect.bisect_right(_js, (s_ + e_) / 2)
                if shots and shots[-1][2] == k_:
                    shots[-1] = (shots[-1][0], _a + f_, k_)
                else:
                    shots.append((_a, _a + f_, k_))
                _a += f_
        # Die Farbe kommt erst, wenn dieser Schnitt wirklich weiterverarbeitet wird (burn) – Testlauf 13.10.: Ein Auftrag
        # mit sicherem Neuschnitt und zweitem Schnitt schickte denselben Clip dreimal durch Resolve (je 30–40 s umsonst).
        RESOLVE_OFFEN[cut] = shots
    return cut, round(total, 1)


def hear(path):
    """Fertigen Ton neu abhören – Wörter mit Zeiten im fertigen Clip."""
    tmp = os.path.join(A.out, "hoerprobe.wav")
    run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vn", "-ac", "1", "-ar", "16000", tmp])  # fertiger Ton ist schon ausgeglichen
    r = mlx_whisper.transcribe(tmp, **ASR, temperature=0.0,
                               initial_prompt=((BRAND + ". ") if BRAND else "") + ("Ähm, äh." if LANG == "de" else ""))
    os.remove(tmp)
    STATS["fillers_heard"] = len(re.findall(r"\b(ähm|äh|öhm|ähh)\b", r.get("text", "").lower()))
    roh = [{"t": x["word"].strip(), "s": float(x["start"]), "e": float(x["end"]), "p": float(x.get("probability", 1.0))}
           for sg in r["segments"] for x in sg.get("words", []) if x["word"].strip()]
    # Bindestrich-Teile sind EIN Wort – wie in der ersten Erkennung (Lauf 09.10. nachts: Im Text stand „Capri-Sonne“, die
    # Hörprobe lieferte „Capri“ + „-Sonne.“; das „-Sonne.“ galt als Wortfetzen am Ende und wurde abgeschnitten – Note 4/10).
    out = []
    for h in roh:
        if out and re.match(r"-\w", h["t"]) and h["s"] - out[-1]["e"] < 0.5:
            out[-1] = {**out[-1], "t": out[-1]["t"] + h["t"], "e": h["e"]}
        else:
            out.append(h)
    return out


OUT_TIME = {}  # Wort-Nummer → Zeitpunkt im fertigen Clip


MISS_I = []  # Nummern der Wörter, die die letzte Hörprobe nicht gehört hat (für die Frage: Schnittschaden oder nur verhört?)


def align(ranges, kept, heard):
    """Untertitel-Zeiten aus dem FERTIGEN Ton (exakt synchron), Text aus dem Original (richtige Schreibweise).
    Liefert zusätzlich die Wörter, die im fertigen Clip nicht mehr zu hören sind."""
    import difflib
    MISS_I.clear()
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
    # Umlaute zählen beim Vergleich nicht (Malo's 12.10.: Text „Burger“, gehört „Bürger“ → galt als fehlendes Wort)
    _uml = str.maketrans("äöü", "aou")
    stem = lambda x: zahl.get(norm(x), norm(x)).translate(_uml)[:5]
    # Zusammenziehungen (Testlauf 12.10., Golden Thai: „gibt's“ im Text, „gibt es“ in der Hörprobe → zweimal „nicht gehört“,
    # dadurch grundlos der grobe Neuschnitt im sicheren Modus): „gibt's“ = „gibt es“ = „gibts“. Die Hörprobe wird an den Text
    # angeglichen – zwei gehörte Wörter werden eins bzw. eines wird zwei –, bevor verglichen wird.
    _z = lambda *xs: "".join(norm(x) for x in xs).replace("es", "s")
    _ein = {_z(w["t"]) for w in kept}
    _zwei = {_z(kept[k]["t"], kept[k + 1]["t"]) for k in range(len(kept) - 1) if norm(kept[k + 1]["t"]) in ("es", "s")}
    _nu = lambda x: norm(x).translate(_uml)
    _ein_t = {_nu(w["t"]): w["t"].strip(".,!?;:…") for w in kept if len(norm(w["t"])) >= 6}
    _paar = {_nu(kept[k]["t"]) + _nu(kept[k + 1]["t"]): (kept[k]["t"], kept[k + 1]["t"]) for k in range(len(kept) - 1)
             if len(norm(kept[k]["t"])) >= 3 and len(norm(kept[k + 1]["t"])) >= 3}
    _ein_u = {_nu(w["t"]) for w in kept}
    _h, _j = [], 0
    while _j < len(heard):
        h = heard[_j]
        if (_j + 1 < len(heard) and norm(heard[_j + 1]["t"]) in ("es", "s") and _z(h["t"], heard[_j + 1]["t"]) in _ein
                and _z(h["t"], heard[_j + 1]["t"]) not in _zwei):
            _h.append({**h, "t": h["t"].strip() + "'s", "e": heard[_j + 1]["e"]})
            _j += 2
            continue
        if norm(h["t"]).endswith("s") and len(norm(h["t"])) > 3 and _z(h["t"]) in _zwei and _z(h["t"]) not in _ein:
            _m = h["s"] + (h["e"] - h["s"]) * 0.7
            _h += [{**h, "t": norm(h["t"])[:-1], "e": _m}, {**h, "t": "es", "s": _m}]
            _j += 1
            continue
        # Zusammengesetzte Wörter (Testlauf 12.10., Malo's): Text „Junior Burger“, gehört „Junior-Burger“ als EIN Wort – und
        # umgekehrt Text „Junior-Box“, gehört „Junior“ „Box“. Beides ist dasselbe; die Hörprobe wird an den Text angeglichen.
        _hn = _nu(h["t"])
        if _hn in _paar and _hn not in _ein_u:      # ein gehörtes Wort = zwei Wörter im Text → teilen (Zeit nach Buchstaben)
            _a, _b = _paar[_hn]
            _m = h["s"] + (h["e"] - h["s"]) * len(norm(_a)) / max(1, len(_hn))
            _h += [{**h, "t": _a, "e": _m}, {**h, "t": _b, "s": _m}]
            _j += 1
            continue
        if _j + 1 < len(heard) and _hn + _nu(heard[_j + 1]["t"]) in _ein_t and _hn + _nu(heard[_j + 1]["t"]) not in _paar \
                and heard[_j + 1]["s"] - h["e"] < 0.4:   # zwei gehörte Wörter = ein Wort im Text → zusammenfügen
            _h.append({**h, "t": _ein_t[_hn + _nu(heard[_j + 1]["t"])], "e": heard[_j + 1]["e"]})
            _j += 2
            continue
        if _hn in _ein_t and "-" in _ein_t[_hn] and "-" not in h["t"]:   # „Juniorbox“ gehört, „Junior-Box“ im Text
            h = {**h, "t": _ein_t[_hn]}
        _h.append(h)
        _j += 1
    heard = _h
    # Bindestrich-Wörter werden TEIL FÜR TEIL verglichen (Malo's 12.10.: Text „Junior-Box“, gehört „Junior“ „Box“ – verglichen
    # wurde nur der letzte Teil, das gehörte „Junior“ blieb „frei“ und wurde als angeblich fehlender Untertitel nachgetragen:
    # „Junior Junior-Box“). Jeder Teil zählt; das Wort beginnt mit seinem ersten und endet mit seinem letzten gehörten Teil.
    _teile = lambda t: [p_ for p_ in t.split("-") if norm(p_)] or [t]
    _h = []
    for h in heard:
        ps = _teile(h["t"])
        if len(ps) > 1:
            _l, _t0 = sum(len(norm(p_)) for p_ in ps) or 1, h["s"]
            for p_ in ps:
                _t1 = _t0 + (h["e"] - h["s"]) * len(norm(p_)) / _l
                _h.append({**h, "t": p_, "s": _t0, "e": _t1})
                _t0 = _t1
        else:
            _h.append(h)
    heard = _h
    want, _wi = [], []
    for _i, w in enumerate(kept):
        for p_ in _teile(w["t"]):
            want.append(stem(p_))
            _wi.append(_i)
    got = [stem(h["t"]) for h in heard]
    sm = difflib.SequenceMatcher(a=want, b=got, autojunk=False)
    match, ende = {}, {}
    for blk in sm.get_matching_blocks():
        for d in range(blk.size):
            match.setdefault(_wi[blk.a + d], blk.b + d)
            ende[_wi[blk.a + d]] = blk.b + d
    # Zweiter Blick auf alles, was „nicht gehört“ wurde (Testlauf 12.10., Malo's: „einen“ im Text, „eine“ gehört; „Malus“ im
    # Text, „Malos“ gehört – beides galt als fehlendes Wort): Steht in der Lücke zwischen den sicher zugeordneten Nachbarn ein
    # noch freies, sehr ähnliches gehörtes Wort, ist es dasselbe Wort – nur anders gebeugt oder geschrieben.
    _frei = set(range(len(heard))) - set(match.values()) - set(ende.values())
    for i in range(len(kept)):
        if i in match or not _frei:
            continue
        lo = max((match[j] for j in match if j < i), default=-1)
        hi = min((match[j] for j in match if j > i), default=len(heard))
        wa = norm(kept[i]["t"].split("-")[-1] or kept[i]["t"])
        best = max(((difflib.SequenceMatcher(a=wa, b=norm(heard[b_]["t"])).ratio(), b_) for b_ in range(lo + 1, hi) if b_ in _frei),
                   default=(0, None))
        if len(wa) >= 3 and best[0] >= 0.75:
            match[i] = best[1]
            _frei.discard(best[1])
    words_out, miss = [], []
    pos_end = lambda i: heard[ende.get(i, match[i])]["e"] if i in match else (est[i][1] if est[i] else 0.0)
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
        # Doppelter Wortteil (Malo's 12.10.: Untertitel „Junior Junior-Box“, gesprochen nur „Junior-Box“): Das Wort wurde nicht
        # gehört, und das nächste – gehörte – Wort fängt genauso an. Dann gab es dieses Wort nie: kein Untertitel, kein Fehler.
        if i not in match and i + 1 < len(kept) and (i + 1) in match and len(norm(w["t"])) >= 4 \
                and norm(kept[i + 1]["t"]).startswith(norm(w["t"])) and len(norm(kept[i + 1]["t"])) > len(norm(w["t"])):
            STATS["doppelt_raus"] = STATS.get("doppelt_raus", 0) + 1
            continue
        # Doppeltes Wort, das nur EINMAL zu hören ist (Test 13.10., Cheesecake „Klein und Groß“: Untertitel „… bestellen, sondern auch
        # sondern auch komplette …“ – der erste Clip endete mit „sondern auch“, der zweite begann damit; im Ton war es nach dem
        # Schnitt nur einmal). Nicht gehört + dasselbe Wort folgt gleich noch einmal und wurde gehört → kein Untertitel, kein Fehler.
        if i not in match and norm(w["t"]) and any(norm(kept[j]["t"]) == norm(w["t"]) and j in match for j in range(i + 1, min(len(kept), i + 4))):
            STATS["doppelt_raus"] = STATS.get("doppelt_raus", 0) + 1
            continue
        pos = None
        if i in match:
            h = heard[match[i]]
            pos = (h["s"], max(heard[ende.get(i, match[i])]["e"], h["s"] + 0.08))
            if est[i] and abs(pos[0] - est[i][0]) > 0.8:  # Zuordnung passt zeitlich nicht → lieber die Schnittliste
                pos = (est[i][0], max(est[i][1], est[i][0] + 0.08))
        else:
            if not re.fullmatch(r"[\W\d_]+", w["t"]) and not w.get("unclear"):  # „&“, „11.05.“ klingen anders; Gemurmel egal
                miss.append(w["t"])
                MISS_I.append(w["i"])
            if est[i]:
                pos = (est[i][0], max(est[i][1], est[i][0] + 0.08))
        if pos:
            OUT_TIME[w["i"]] = pos[0]
        # Unverständliches: Ton bleibt, aber kein (falscher) Untertitel. Ausnahme (Malo's 09.10., 0,6 s ohne Untertitel über
        # „dazu eine“): Hört die Hörprobe am FERTIGEN Ton genau dieses Wort, ist es verständlich – dann bekommt es seinen Untertitel.
        if pos and (not w.get("unclear") or i in match):
            words_out.append((w["t"], pos[0], pos[1]))
    # Was im fertigen Clip klar zu hören ist, aber noch keinen Untertitel hat (z. B. ein Satz, den die erste Erkennung im
    # Durcheinander überhört hat – der Ton blieb drin, der Text fehlte): aus der Hörprobe nachtragen. Nur in echten Lücken,
    # nie Füllwörter, nie einzelne Kurz-Wörter (Rauschen).
    used = {b_ for i_ in match for b_ in range(match[i_], ende.get(i_, match[i_]) + 1)}   # alle Teile eines Bindestrich-Worts
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
        # „dort steht schon ein Untertitel“ = ein vorhandener Untertitel deckt mindestens ein Drittel der Stelle ab. (Vorher
        # reichte Nachbarschaft von 0,15 s – das klar gesprochene Schlusswort „Capri-Sonne“ direkt nach „eine“ blieb deshalb
        # ohne Untertitel, Lauf 10.10.)
        deck = sum(max(0.0, min(b, x[2]) - max(a, x[1])) for x in words_out)
        if deck >= 0.34 * max(0.1, b - a):
            continue
        at = next((k for k, x in enumerate(words_out) if x[1] > a), len(words_out))  # vorhandene Reihenfolge bleibt unangetastet
        words_out[at:at] = [(h["t"], h["s"], max(h["e"], h["s"] + 0.08)) for h in g]
        added += [h["t"] for h in g]
    if added:
        STATS["captions_added"] = added[:30]
    for k in range(1, len(words_out)):  # Reihenfolge = Sprechreihenfolge; Zeiten nur nach vorne schieben, nie umsortieren
        if words_out[k][1] < words_out[k - 1][1] + 0.04:
            tx, s0, e0 = words_out[k]
            words_out[k] = (tx, words_out[k - 1][1] + 0.04, max(e0, words_out[k - 1][1] + 0.12))
    # Golden-Thai-Test 07.10.: „Während alle“ wurde gesprochen, stand aber nicht im Untertitel – die Erkennung hatte die zwei
    # Wörter einem früheren, gestrichenen Anlauf zugeschlagen. Hört die Hörprobe am ANFANG eines Abschnitts Wörter, die keinem
    # behaltenen Wort zugeordnet sind, und stehen genau diese Wörter kurz davor im Original (als gestrichen), dann gehören sie
    # zum Satz und bekommen ihren Untertitel. Nur diese eng belegte Ergänzung – frei „dazugehörte“ Wörter bleiben tabu (Regel 101).
    try:
        used, kept_i, dazu = {b_ for i_ in match for b_ in range(match[i_], ende.get(i_, match[i_]) + 1)}, {w["i"] for w in kept}, 0
        for s_, e_, o_ in offs:
            fk = next((i for i, w in enumerate(kept) if s_ - 0.05 <= w["s"] < e_ and i in match), None)
            if fk is None:
                continue
            hb, cand = match[fk], []
            j = hb - 1
            while j >= 0 and j not in used and heard[j]["s"] >= o_ - 0.05 and heard[hb]["s"] - heard[j]["s"] < 1.5:
                cand.insert(0, j)
                j -= 1
            if not cand:
                continue
            # (nie ein Füll-Laut: Testlauf 13.10., Cheesecake-Umfrage – ein gestrichenes „Äh“ kam so als Untertitel zurück)
            nah = {norm(w["t"]): w["t"] for w in WORDS if s_ - 8.0 <= w["s"] < s_ + 0.3 and w["i"] not in kept_i and norm(w["t"])
                   and norm(w["t"]) not in FILLER}
            if not all(norm(heard[j]["t"]) in nah for j in cand):
                continue
            at = next((k for k, wo in enumerate(words_out) if wo[1] >= heard[hb]["s"] - 0.02), len(words_out))
            neu = [(nah[norm(heard[j]["t"])].rstrip(".,!?…"), heard[j]["s"], max(heard[j]["e"], heard[j]["s"] + 0.08)) for j in cand]
            words_out[at:at] = neu
            dazu += len(neu)
        if dazu:
            STATS["untertitel_aus_hoerprobe"] = dazu
    except Exception as e:  # noqa: BLE001
        log(f"Untertitel-Ergänzung aus der Hörprobe übersprungen ({e})")
    # Satzanfang groß (Testlauf 12.10., Malo's: „… macht. jetzt ist unser Junior Burger fertig. das Spielzeug …“ – das „Und“
    # davor war herausgeschnitten, das nächste Wort blieb klein). Nach Punkt, Ausrufe- oder Fragezeichen und ganz am Anfang.
    for _k, (_t, _s, _e) in enumerate(words_out):
        if _t[:1].islower() and (_k == 0 or re.search(r"[.!?]$", words_out[_k - 1][0].strip())):
            words_out[_k] = (_t[:1].upper() + _t[1:], _s, _e)
    return words_out, miss


SFX_DIR = os.environ.get("VELOX_SFX", os.path.join(MASCHINE, "sfx"))
EMOJI_DIR = os.environ.get("VELOX_EMOJI", os.path.join(MASCHINE, "emoji"))
EMOJIS = {"😋": "1f60b", "🔥": "1f525", "😍": "1f60d", "🤤": "1f924", "😂": "1f602", "🤯": "1f92f", "😮": "1f62e", "🤔": "1f914",
          "👀": "1f440", "💸": "1f4b8", "💰": "1f4b0", "🎉": "1f389", "🏆": "1f3c6", "💯": "1f4af", "👍": "1f44d", "👏": "1f44f",
          "❤️": "2764", "✅": "2705", "❌": "274c", "⭐": "2b50", "🍰": "1f370", "🍔": "1f354", "🍕": "1f355", "☕": "2615",
          "🍫": "1f36b", "🍌": "1f34c", "🍓": "1f353", "🎁": "1f381", "📍": "1f4cd", "⏰": "23f0", "🚀": "1f680", "😎": "1f60e",
          # Inhaber 07.10.: „Bei ‚Verdauung komplett umkrempeln‘ kam eine Rakete – das passt nicht.“ Mehr Auswahl, damit
          # die KI ein Emoji findet, das zur Aussage passt (alle aus der System-Schrift erzeugt, kein Download).
          "🔄": "1f504", "💪": "1f4aa", "🥗": "1f957", "🥑": "1f951", "🌱": "1f331", "💡": "1f4a1", "⚠️": "26a0", "📈": "1f4c8",
          "🧠": "1f9e0", "❓": "2753", "✨": "2728", "🙌": "1f64c", "😱": "1f631", "🤝": "1f91d", "📞": "1f4de", "🛒": "1f6d2",
          "🏠": "1f3e0", "🚗": "1f697", "✂️": "2702", "🎬": "1f3ac", "📱": "1f4f1", "💧": "1f4a7", "🥤": "1f964", "🍜": "1f35c",
          "🌶️": "1f336", "🧀": "1f9c0", "🥩": "1f969", "🍟": "1f35f", "🥒": "1f952", "🧅": "1f9c5", "🍞": "1f35e", "🥭": "1f96d",
          "🍵": "1f375", "🧊": "1f9ca", "😴": "1f634", "🤢": "1f922", "💚": "1f49a", "📅": "1f4c5", "🎯": "1f3af", "🤫": "1f92b",
          "👇": "1f447", "👉": "1f449", "🙏": "1f64f", "😅": "1f605", "🤩": "1f929", "💥": "1f4a5", "🧾": "1f9fe", "🏋️": "1f3cb",
          "💤": "1f4a4", "🩺": "1fa7a"}
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
# Weitere Stücke kommen ohne Programmänderung dazu: Datei <name>.mp3 in den Musik-Ordner legen und in musik/katalog.json
# eintragen: {"<name>": "Genre, Stimmung – wofür; küche: thai|türkisch|… (falls landestypisch)"}.
try:
    _kat = os.path.join(MUSIC_DIR, "katalog.json")
    if os.path.exists(_kat):
        MUSIC.update({str(k): str(v) for k, v in json.load(open(_kat, encoding="utf-8")).items()})
except Exception as _ek:  # noqa: BLE001
    log(f"Musik-Katalog nicht lesbar: {_ek}")


def pick_music(kept):
    """Passende Hintergrundmusik: bei --music auto wählt die KI nach Inhalt und Stimmung, sonst der genannte Titel."""
    if A.music == "none" and not (PLAN or {}).get("zeigen"):  # stumme Zeige-Strecken brauchen Musik (Regel 114)
        return None
    have = {n: d for n, d in MUSIC.items() if os.path.exists(os.path.join(MUSIC_DIR, f"{n}.mp3"))}
    if A.music in have:
        return os.path.join(MUSIC_DIR, f"{A.music}.mp3")
    if not have:
        return None
    # Inhaber 09.10.: „Die Musik passt nicht wirklich. Sie soll modern sein und zum Video und zum Unternehmen passen – keine
    # Mittelaltermusik bei einem modernen Burgerladen. Bei einem thailändischen oder türkischen Restaurant darf es
    # landestypische Musik sein. Das musst du der Software beibringen, wie sie wo zu entscheiden hat.“
    # Entscheidung in zwei Schritten: erst das PROFIL (was würde passen – unabhängig vom Bestand), dann das beste vorhandene
    # Stück dazu. Das Wunsch-Profil steht im Protokoll (STATS „musik_wunsch“), damit sichtbar wird, welche Stücke fehlen.
    res = gemini(
        "Du bist Musik-Redakteur für Kurzvideos (TikTok/Reels) von Unternehmen." + NISCHE_HINT + " Wähle die Hintergrundmusik.\n"
        "REGELN:\n"
        "1) Grundsatz MODERN: aktuelle, junge Klangwelt (Hip-Hop-/Trap-Beat, House, Afro, Lo-Fi, moderner Pop/Funk). Nie "
        "altmodisch, nie Mittelalter/Folklore/Klassik/Fahrstuhlmusik, nichts Kitschiges.\n"
        "2) Zum UNTERNEHMEN: Burger, Street-Food, Döner-Imbiss, Sneaker, Barber, Fitness → Hip-Hop/Trap/Funk mit Druck. Café, "
        "Bäckerei, Dessert, Beauty, Wellness → Lo-Fi, weicher Pop, entspannt. Bar, Club, Shisha, Mode → House/Afro/Deep. "
        "Familie, Kinder, Eis → fröhlich, hell. Handwerk, Immobilien, Beratung, Auto → modern-neutral, selbstbewusst, nicht verspielt.\n"
        "3) LANDESKÜCHE: Ist das Unternehmen klar landestypisch (thailändisch, türkisch, arabisch, italienisch, indisch, "
        "griechisch, mexikanisch, japanisch …), passt ein Stück mit Klängen dieses Landes – am liebsten modern produziert "
        "(landestypische Instrumente über modernem Beat), traditionell nur, wenn es nichts Modernes gibt. Nie die Musik eines "
        "ANDEREN Landes (kein arabisches Stück für ein Thai-Restaurant).\n"
        "4) Zum VIDEO: Zubereitung/Bildfolge → gleichmäßiger Beat mit Zug, auf den sich schneiden lässt – das geht VOR der "
        "Zielgruppe: Auch bei einem Kinder- oder Familienprodukt bekommt eine Zubereitung keinen niedlichen Gute-Laune-Pop, "
        "sondern einen modernen Beat (Hip-Hop, Funk, House), wie ihn Food-Kanäle für Zubereitungen nehmen (Inhaber 09.10. zu "
        "Malo's: „Die Musik passt nicht zur Zubereitung“ – gewählt war fröhlicher Kinder-Pop). Monolog/Erklärung → "
        "ruhig, drängt sich nicht vor die Stimme. Umfrage/Challenge → verspielt, energisch. Hochwertige Produktbilder → edel, reduziert.\n"
        "5) Gibt es im Bestand kein Stück, das Regel 3 erfüllt, nimm das beste MODERNE, neutrale Stück – lieber neutral als "
        "falsches Land oder falsche Stimmung.\n"
        "Antworte mit: wunsch = das ideale Profil in wenigen Wörtern (Genre, Land falls landestypisch, Stimmung), musik = das "
        "beste vorhandene Stück, passt = true, wenn es dem Wunsch wirklich entspricht (nicht nur Notlösung).\n"
        "Stil des Schnitts: " + {"clean": "ruhig, sauber", "viral": "schnell, laut", "premium": "hochwertig, kinoreif"}[A.style] + ". "
        + ("Im Video gibt es eine stumme Bildfolge (Zubereitung) – dort trägt die Musik allein." if (PLAN or {}).get("zeigen") else ""),
        "Bestand:\n" + "\n".join(f"{n}: {d}" for n, d in have.items()) + "\n\nTranskript: " + " ".join(w["t"] for w in kept)[:3000],
        {"type": "object", "properties": {"wunsch": {"type": "string"}, "musik": {"type": "string", "enum": list(have)},
                                          "passt": {"type": "boolean"}}, "required": ["wunsch", "musik", "passt"]})
    if res:
        STATS["musik_wunsch"] = {"wunsch": str(res.get("wunsch") or "")[:80], "passt": bool(res.get("passt"))}
        log(f"Musik: Wunsch „{str(res.get('wunsch') or '')[:60]}“ → {res.get('musik')}" + ("" if res.get("passt") else " (Notlösung – passendes Stück fehlt im Bestand)"))
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


PREMX = {}  # Premium-Zusätze aus gemini_effects: genannter Ort, Handlungsaufruf (für Orts-Marke und Schlusskarte)
ZOOM_WORDS = set()  # Wortnummern mit Produkt/Gericht → kurzer Zoom-Punch (gemini_effects füllt, zoom_punches nutzt)


def clean_extras(kept):
    """Clean (Inhaber 10.10.: „Clean ist zu wenig – ein paar Sachen dazu, aber klar anders als Viral“): keine Emojis, keine
    Sounds, keine Zooms – nur zwei ruhige, dunkle Marken: der genannte Ort (einmal, oben) und am Ende der Handlungsaufruf.
    Ein kleiner KI-Aufruf; ohne KI bleibt Clean wie bisher."""
    PREMX.clear()
    res = gemini(
        "Du bist Editor für ruhige, seriöse Kurzvideos. Aus dem Transkript: ort = wird ein konkreter Ort genannt (Stadt, "
        "Stadtteil, Straße, Platz, Land), dann dessen Wortnummer und Name (z. B. „Mannheim“) – sonst weglassen; aufruf = der "
        "Handlungsaufruf dieses Videos in höchstens 4 Wörtern, passend zu Branche und Inhalt (z. B. „Jetzt vorbeikommen“, "
        "„Folgen für mehr“, „Termin sichern“) – leer lassen, wenn das Video zu nichts aufruft. Wörter als [Nummer]Wort.",
        " ".join(f"[{w['i']}]{w['t']}" for w in kept if w["i"] in OUT_TIME),
        {"type": "object", "properties": {"ort": {"type": "object", "properties": {"i": {"type": "integer"}, "text": {"type": "string"}}},
                                          "aufruf": {"type": "string"}}, "required": ["aufruf"]})
    if res:
        PREMX.update(ort=res.get("ort") or None, aufruf=" ".join(str(res.get("aufruf") or "").split()[:4])[:30])


def gemini_effects(kept, times):
    """Viral: KI setzt wenige, passende Akzente – Emoji + Sound + Zoom-Punch an Pointen (Preis → Kaching, Bewertung → Ding …).
    Läuft VOR dem Rendern (Zeiten aus der Schnittliste), damit die Zoom-Punches genau auf diese Momente fallen."""
    OUT_TIME = times  # noqa: N806  (gleiche Zeitbasis wie später im fertigen Clip)
    res = gemini(
        "Du bist Editor für virale Kurzvideos." + NISCHE_HINT + " Setze SPARSAM Akzente an die stärksten Momente des Transkripts (Pointen, Zahlen, "
        "Preise, Bewertungen, überraschende Reaktionen, das Produkt). Höchstens ein Akzent pro 6 Sekunden, insgesamt 2–6. "
        "Je Akzent: Wortnummer (das Wort, bei dem er erscheint), „text“ = das Schlüsselwort bzw. die Zahl genau wie gesagt "
        "(1–3 Wörter, z. B. „Kaiserschmarrn“, „10 von 10“, „150 Millionen“, „12 Euro“), ein passendes Emoji aus dieser Liste: "
        + " ".join(EMOJIS) +
        " – das Emoji muss zur AUSSAGE an dieser Stelle passen (Verdauung/Umstellung → 🔄 oder 🩺, Tipp → 💡, Warnung → ⚠️, "
        "Essen → das passende Lebensmittel); 🚀 und 🔥 nur, wenn wirklich Tempo/Begeisterung gemeint ist. Passt keines "
        "eindeutig, das Feld emoji leer lassen (dann kommt nur der Sound)" +
        " und ein Sound aus: pop (Standard), ding (Bewertung/Pointe), kaching (Geld/Preis/Gewinn), whoosh (Überraschung), click "
        "(Aufzählung). Außerdem „zoom“: Wortnummern, bei denen ein Produkt, Gericht oder eine Sorte genannt bzw. gezeigt wird "
        "(z. B. „Classic“, „Topping nach Wahl“, „Smashburger“) – dort zoomt das Bild kurz rein (ohne Emoji/Sound), höchstens "
        "einer pro 3 Sekunden. "
        + ("PREMIUM: Hier bekommt JEDER genannte Preis/Betrag und JEDES aufgezählte Produkt, Angebot oder Stichwort einen eigenen "
           "Akzent – genau bei dem Wort, bei dem es gesagt wird („für unter 10 Euro“ → text „10 €“, sfx kaching; „drei Slices“ → "
           "text „3 Slices“ – bei Mengen die Zahl IMMER mit auf die Karte; „Eislatte“ → text „Eislatte“; nimm das genaue Produkt („Strawberry Matcha“), nicht den Oberbegriff („Getränk“). Bis zu 10 Akzente, mindestens 1,5 Sekunden Abstand; text in normaler "
           "Schreibweise (nicht in Großbuchstaben), bei Preisen immer mit „€“. Zusätzlich: ort = wird ein Ort genannt (Stadt, "
           "Stadtteil, Straße, Platz), dann dessen Wortnummer und Name (z. B. „Mannheim“); aufruf = der Handlungsaufruf dieses "
           "Videos in höchstens 4 Wörtern, passend zu Branche und Inhalt (z. B. „Jetzt vorbeikommen“, „Folgen & kommentieren“, "
           "„Termin sichern“) – leer lassen, wenn das Video zu nichts aufruft. " if PREM else "")
        + "Wörter als [Nummer|Sekunde]Wort.",
        " ".join(f"[{w['i']}|{OUT_TIME[w['i']]:.1f}s]{w['t']}" for w in kept if w["i"] in OUT_TIME),
        {"type": "object", "properties": {"fx": {"type": "array", "items": {"type": "object", "properties": {
            "i": {"type": "integer"}, "text": {"type": "string"}, "emoji": {"type": "string"}, "sfx": {"type": "string"}},
            "required": ["i", "text", "emoji", "sfx"]}},
            "zoom": {"type": "array", "items": {"type": "integer"}},
            "ort": {"type": "object", "properties": {"i": {"type": "integer"}, "text": {"type": "string"}}},
            "aufruf": {"type": "string"}},
         "required": ["fx"]})
    if PREM and res:
        PREMX.clear()
        PREMX.update(ort=res.get("ort") or None, aufruf=" ".join(str(res.get("aufruf") or "").split()[:4])[:30])
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
CUT_TIMES = []   # Schnitt-Zeitpunkte im fertigen Clip (burn füllt) – Karten enden am Schnitt
CARDS = []       # Premium: Stichwort-Karten dieses Clips (Zeit, Text, Symbol, Platz) – premium_events plant, burn zeichnet
CARD_PITCH = 146  # Höhe einer Karte samt Abstand
# Inhaber 07.10.: „Bei Strawberry Matcha sollte eine Erdbeere im Feld sein.“ Liefert die KI kein Symbol, wählt die Karte
# selbst eines nach dem Stichwort (nur aus den vorhandenen Emoji-Bildern); erst wenn nichts passt, bleibt der neutrale Punkt.
CARD_ICON = [(r"erdbeer|strawberry|beere", "🍓"), (r"cheesecake|kuchen|torte|cake|slice", "🍰"), (r"burger|patty", "🍔"),
             (r"pizza", "🍕"), (r"matcha|latte|kaffee|coffee|tee\b|espresso|cappuccino", "☕"), (r"schoko|choco|nutella", "🍫"),
             (r"banan", "🍌"), (r"€|euro|preis|rabatt|sparen|günstig|gratis|kostenlos", "💸"), (r"geschenk|gewinn|verlos", "🎁"),
             (r"uhr|zeit|minuten|stunden|schnell", "⏰"), (r"standort|adresse|straße|mannheim|hier", "📍"),
             (r"neu\b|launch|start", "🚀"), (r"top|beste|nummer 1|sieger", "🏆"), (r"\d", "💯")]
GRUEN = "&H0064E15A"  # Zähler am Ziel


def card_png(text, emoji, out, nr=None, art=None):
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
    acc = tuple(int(A.color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    dunkel = art == "schluss" or not LOUD   # Clean: alle Marken dunkel und ruhig; Schlusskarte: dunkel mit Rand in der Akzentfarbe, weiße Schrift, Pfeil
    d.rounded_rectangle((P, P, P + W, P + H), radius=36 * sc, fill=(16, 16, 20, 240) if dunkel else (250, 250, 252, 240),
                        outline=acc + (255,) if dunkel else (255, 255, 255, 255), width=(4 if dunkel else 2) * sc)
    d.rounded_rectangle((P + 16 * sc, P + 16 * sc, P + (h - 16) * sc, P + (h - 16) * sc), radius=24 * sc, fill=acc + (255,))
    ico = os.path.join(EMOJI_DIR, f"{EMOJIS.get(emoji, '')}.png")
    if emoji and os.path.exists(ico):
        e = Image.open(ico).convert("RGBA")
        e.thumbnail((72 * sc, 72 * sc), Image.LANCZOS)
        im.alpha_composite(e, (P + (h * sc - e.width) // 2, P + (h * sc - e.height) // 2))
    elif dunkel:  # Pfeil nach rechts
        cx_, cy_ = P + h * sc // 2, P + H // 2
        d.polygon([(cx_ - 20 * sc, cy_ - 24 * sc), (cx_ + 22 * sc, cy_), (cx_ - 20 * sc, cy_ + 24 * sc)], fill=(16, 16, 20, 255))
    else:
        d.ellipse((P + 40 * sc, P + 40 * sc, P + (h - 40) * sc, P + (h - 40) * sc), fill=(18, 18, 22, 255))
    d.text((P + (h + 10) * sc, P + H // 2), text, font=font, fill=(255, 255, 255, 255) if dunkel else (18, 18, 22, 255), anchor="lm")
    if nr:  # Nummer der Aufzählung: kleiner dunkler Kreis an der linken oberen Ecke
        r_ = 26 * sc
        d.ellipse((P - 8 * sc, P - 8 * sc, P - 8 * sc + 2 * r_, P - 8 * sc + 2 * r_), fill=(16, 16, 20, 255), outline=(255, 255, 255, 255), width=2 * sc)
        d.text((P - 8 * sc + r_, P - 8 * sc + r_), str(nr), font=ImageFont.truetype(os.path.join(FONTS, "Montserrat-Bold.otf"), 30 * sc),
               fill=(255, 255, 255, 255), anchor="mm")
    im.resize((im.width // sc, im.height // sc), Image.LANCZOS).save(out)
    return out, p
NUM = re.compile(r"(\d{1,3}(?:[.\s]\d{3})+|\d+(?:,\d+)?)")


def premium_events(fx, words_out, title, voll=True, ruhig=False):
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
    # Rauchtest 07.10.: Der Zähler „3 CHEESECAKES“ stand mitten auf dem Gesicht (die alte Stelle „390 px über den Untertiteln“
    # prüfte das Gesicht nur grob). Der Zähler steht jetzt GENAU an der Stelle der Untertitel – die ist immer gesichtsfrei und im
    # freien Rahmen, und die Untertitel pausieren ohnehin, solange er läuft.
    py = cy
    if lines and LAY["title"]:
        for li, line in enumerate(lines):
            ev += text3d(line, 540, LAY["hy"] + 15 + int(fs_t * 0.54) + li * int(fs_t * 1.08), 0.05 + li * 0.08, 3.0, fs_t,
                         color=WHITE if li == 0 else ACC)
    for f in fx:
        t0, txt = f["t"], (f.get("text") or "").strip().upper()
        if not txt:
            continue
        m = NUM.search(txt)
        # Premium-Lauf 07.10.: „5 CLASSIC“ und „3 CHEESECAKES“ zählten von 0 hoch (albern), der eigentliche Preis „unter 10 Euro“
        # bei Sekunde 2 fiel dagegen weg (alte Sperre „nicht in den ersten 3 s“ – der Zähler steht aber an der Untertitel-Stelle,
        # nicht beim Titel). Hochgezählt werden nur Preise, Prozente und große Zahlen; kleine Mengen werden bei Premium zur Karte.
        zaehlbar = bool(m) and (bool(re.search(r"€|euro|eur\b|%|prozent", txt, re.I))
                                or float(m.group(1).replace(".", "").replace(" ", "").replace(",", ".")) >= 100)
        if m and not zaehlbar:
            m = None
            if not voll:
                continue  # Viral: Zähler nur für Preise (wie im Website-Beispiel) – „3 Cheesecakes“ bleibt normaler Untertitel
        if m:  # Zähler
            raw = m.group(1)
            val = float(raw.replace(".", "").replace(" ", "").replace(",", "."))
            dec = "," in raw
            start = val - 15 if 1900 <= val <= 2100 and not dec else 0.0
            pre, post = txt[:m.start()].strip(), txt[m.end():].strip()
            fs = fit_fs(f"{pre} {raw} {post}".strip(), 660, 170)
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
        elif voll or VIRAL:  # Stichwort/Produkt → Karte mit Symbol, erscheint beim gesprochenen Wort (Stil-Muster vom Inhaber bestätigt 07.10.)
            # Seit 10.10. auch bei Viral (Inhaber: Clean bekommt etwas dazu, also muss Viral sich wieder abheben; Premium hat
            # inzwischen den neuen Baukasten). Bei Viral trägt die Karte das Emoji – es erscheint dann nicht noch einmal einzeln.
            ktxt = " ".join((f.get("text") or "").split())[:26]
            if not re.search(r"\d", ktxt):  # Inhaber 07.10.: sagt er „drei Cheesecakes“, steht „3 Cheesecakes“ auf der Karte
                zw = dict(ein="1", eine="1", einen="1", zwei="2", drei="3", vier="4", fünf="5", sechs="6", sieben="7", acht="8", neun="9", zehn="10")
                davor = [w[0] for w in words_out if t0 - 1.6 <= w[1] <= t0 + 0.05][-3:]
                zahl = next((zw.get(norm(x), norm(x)) for x in reversed(davor) if norm(x).isdigit() or norm(x) in ("zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun", "zehn")), None)
                if zahl:
                    ktxt = f"{zahl} {ktxt}"[:26]
            ico = f.get("emoji") or next((e for pat, e in CARD_ICON if re.search(pat, ktxt, re.I)), None)
            # Inhaber 08.10.: „Er sagt schon die Nummer 33 und das wird erst später eingeblendet.“ Beginnt die Karte mit einer
            # Zahl, die kurz vorher gesprochen wird, erscheint sie bei der Zahl – nicht erst beim Namen danach.
            mz = re.match(r"\d+", ktxt)
            tk = next((w[1] for w in words_out if mz and t0 - 1.8 <= w[1] < t0 and re.sub(r"\D", "", str(w[0])) == mz.group(0)), None)
            # Testlauf 13.10.: Karten für „frisch“, „ein“, „verlosen“, „folgen“, „belgischer“ – einzelne klein geschriebene
            # Wörter (Eigenschaft, Tätigkeit, Füllwort) sind kein Stichwort und standen teils ohne Symbol da. Eine Karte bekommt
            # nur ein Produkt, Name, Ort, Preis oder Angebot: also ein Hauptwort (groß geschrieben), eine Zahl oder mehrere Wörter.
            if " " not in ktxt and not re.search(r"\d", ktxt) and ktxt[:1].islower():
                continue
            karten.append({"t": tk if tk is not None else t0, "text": ktxt, "emoji": ico})
    # Karten, die kurz nacheinander fallen (Aufzählung), stapeln sich und bleiben gemeinsam stehen
    CARDS.clear()
    gruppen = []
    for k in karten:
        schnitt = any(gruppen and gruppen[-1][-1]["t"] < c_ <= k["t"] for c_ in CUT_TIMES)
        if gruppen and k["t"] - gruppen[-1][-1]["t"] <= 3.2 and len(gruppen[-1]) < 4 and not schnitt:
            gruppen[-1].append(k)
        else:
            gruppen.append([k])
    half = CAPFS[0] * 0.6

    def platz(n, ta, tb):
        """Oberkante für n Karten im Zeitraum ta–tb: unter den Untertiteln, sonst darüber, sonst oben über dem Kopf.
        Inhaber 08.10. (Premium, „er sagt schon die Nummer 33 und das wird erst später eingeblendet“): Früher wichen die
        UNTERTITEL der Karte, wenn kein Platz war – vier Sekunden lang stand kein gesprochenes Wort im Bild. Untertitel
        pausieren nie mehr; findet eine Karte keinen freien Platz, gibt es None zurück."""
        h = (n - 1) * CARD_PITCH + 130
        # Inhaber 12.10. (Malo's, Viral – Karte „Spielzeug“ lag genau auf dem Untertitel „SPIELZEUG“): „Nichts soll sich
        # überlappen.“ Der dritte Platz („oben“) wurde nie gegen die Untertitel geprüft – standen sie selbst oben, lag die
        # Karte darauf. Jetzt gilt für JEDEN Platz: mindestens 10 px Abstand zur Untertitel-Zeile, sonst lieber keine Karte.
        frei = lambda y0: SAFE_T <= y0 and y0 + h <= SAFE_B and not hits_face(210, y0, 870, y0 + h, ta, tb) \
            and not _cut(y0, y0 + h, cy - half - 10, cy + half + 10) \
            and not (LAY.get("trect") and ta < 3.6 and _cut(y0, y0 + h, LAY["trect"][1] - 10, LAY["trect"][3] + 10))
        unten = min(cy + half + 26, SAFE_B - h)          # darf bis auf 10 px an die Untertitel heranrücken
        for y0 in ([unten] if unten >= cy + half + 10 else []) + [cy - half - 26 - h, SAFE_T + 10]:
            if frei(y0):
                return y0
        return None

    for g in gruppen:
        ta, tb = g[0]["t"], g[-1]["t"] + 2.2
        # Inhaber 07.10.: „Nach dem Cut bleibt ‚Toppings‘ noch stehen, obwohl er schon den Cancake hebt – das muss weg.“
        # Eine Karte gehört zu ihrer Einstellung: Am nächsten Schnitt ist sie verschwunden (mind. 0,9 s war sie zu sehen).
        nc = next((c_ for c_ in CUT_TIMES if c_ >= g[-1]["t"] + 0.9), None)
        if nc is not None and nc < tb + 0.28:
            tb = nc - 0.30
        y0 = platz(len(g), ta, tb)
        if y0 is not None:
            for r, k in enumerate(g):
                # Inhaber 07.10.: nummerierte Schritte – in einer Aufzählung trägt jede Karte ihre Nummer („1“, „2“, „3“)
                CARDS.append({**k, "y": int(y0 + r * CARD_PITCH), "bis": tb, "nr": r + 1 if len(g) > 1 else None})
            continue
        # Der Stapel passt nicht → die Karten kommen einzeln nacheinander (jede löst die vorige ab)
        for r, k in enumerate(g):
            bis = min(tb, g[r + 1]["t"] - 0.05) if r + 1 < len(g) else tb
            y1 = platz(1, k["t"], bis)
            if y1 is None or bis - k["t"] < 0.8:
                log(f"Karte „{k['text']}“ weggelassen – kein freier Platz neben Gesicht und Untertiteln")
                continue
            CARDS.append({**k, "y": int(y1), "bis": bis, "nr": r + 1 if len(g) > 1 else None})
    if voll or ruhig:   # ruhig = Clean: nur Orts-Marke und Schlusskarte (dunkel), sonst nichts
        ende = words_out[-1][2] if words_out else 0.0
        # Orts-Marke (Inhaber 07.10.): fällt ein Ort, erscheint oben eine Karte mit Stecknadel – wenn dort frei ist
        ort = PREMX.get("ort") or {}
        t_o = OUT_TIME.get(ort.get("i")) if isinstance(ort.get("i"), int) else None
        o_txt = " ".join(str(ort.get("text") or "").split())[:24]
        if t_o is not None and o_txt and 0.5 < t_o < ende - 1.5:
            y_o = SAFE_T + 6 if not (LAY.get("trect") and t_o < 3.4) else int(LAY["trect"][3]) + 16
            if not hits_face(210, y_o, 870, y_o + CARD_PITCH, t_o, t_o + 2.4) and not _cut(y_o, y_o + CARD_PITCH, cy - half - 10, cy + half + 10) \
                    and not any(_cut(y_o, y_o + CARD_PITCH, c["y"], c["y"] + CARD_PITCH) and c["t"] < t_o + 2.4 and c["bis"] > t_o for c in CARDS):
                CARDS.append({"t": t_o, "text": o_txt, "emoji": "📍", "y": y_o, "bis": t_o + 2.4, "art": "ort"})
        # Schlusskarte (Inhaber 07.10.): in den letzten Sekunden der Handlungsaufruf als eigene, dunkle Karte
        ruf = PREMX.get("aufruf") or ""
        if ruf and ende >= 8.0:
            ta = max(ende - 1.9, max([c["bis"] + 0.1 for c in CARDS] + [0.0]))
            if ende - ta >= 1.1:
                y0 = platz(1, ta, ende + 0.4)
                if y0 is not None:   # kein freier Platz → lieber keine Schlusskarte als eine über dem Gesicht
                    CARDS.append({"t": ta, "text": ruf, "emoji": None, "y": int(y0), "bis": ende + 0.4, "art": "schluss"})
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


BELEGT = []  # Regel 125: alles, was in diesem Clip schon eingeblendet wird – (x0, y0, x1, y1, von, bis, was)


def belegt(x0, y0, x1, y1, t0, t1, rand=12):
    """Liegt in diesem Rechteck und Zeitraum schon eine andere Einblendung (Karte, Logo, Sticker, Emoji)? Inhaber 12.10.:
    „Nichts soll sich überlappen.“ Bisher prüfte jede Einblendung nur gegen Gesicht, Titel und Untertitel – nicht
    gegeneinander. Wer zuerst seinen Platz hat, behält ihn; was später kommt und kollidieren würde, entfällt."""
    return next((b[6] for b in BELEGT if x0 - rand < b[2] and x1 + rand > b[0] and y0 - rand < b[3] and y1 + rand > b[1]
                 and t0 < b[5] and t1 > b[4]), None)


def burn(idx, cut, words_out, title, ranges=None, kept=None):
    """2. Durchgang: Untertitel (und bei Viral den Hook-Titel) einbrennen, web-sicheres MP4."""
    if RESOLVE and cut in RESOLVE_OFFEN:
        resolve_farbe(cut, RESOLVE_OFFEN.pop(cut))
    out = os.path.join(A.out, f"clip-{idx}.mp4")
    ass = os.path.join(A.out, f"clip-{idx}.ass")
    try:   # für die Messlatte (messlatte.py): welche Wörter laut Schnitt im Video sein sollen, mit Zeit im fertigen Clip
        json.dump([[w_[0], round(float(w_[1]), 3), round(float(w_[2]), 3)] for w_ in words_out],
                  open(os.path.join(A.out, f"clip-{idx}.woerter.json"), "w"), ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass
    # Premium-Grafiken neu (09.10.2026, premium2.py): KI wählt Bausteine aus den Vorbildern des Inhabers (Vollbild-Karte, Sprecher
    # als Karte, Text hinter der Person, Zähler, Listen …), gezeichnet mit dem Remotion-Baukasten. B-Roll und Bilder aus dem Schnittplan gehen mit (Vollbild, Karte unter dem Sprecher oder Handy-Mockup).
    # Seit 10.10.2026 Standard für Premium (Inhaber: „soll rein“); VELOX_PREMIUM2=0 schaltet auf den alten Weg zurück. Mit
    # gewünschter Hintergrundmusik läuft vorerst der alte Weg – der neue mischt noch keine Musik.
    if PREM and os.environ.get("VELOX_PREMIUM2", "1") != "0":
        try:
            import premium2
            _br = [dict(file=b_["file"], t=out_at(b_["t_src"]), dur=b_.get("dur", 2.4)) for b_ in (PLAN or {}).get("broll") or []]
            _bi = [dict(file=b_["file"], t=out_at(b_["t_src"])) for b_ in (PLAN or {}).get("bilder") or []]
            _br, _bi = [x for x in _br if x["t"] is not None], [x for x in _bi if x["t"] is not None]
            _ct, _t0 = [], 0.0
            for _rs, _re in (ranges or [])[:-1]:
                _t0 += olen(_rs, _re)
                _ct.append(round(_t0, 3))
            _r = premium2.render(cut, words_out, title, out, lambda sy, us, sc: gemini(sy, us, sc, timeout=120, deep=True),
                                 cut_times=(),   # Zoom-Stufen bringt der Schnitt schon mit (Inhaber: Premium-Zooms passen) – kein zweiter Zoom obendrauf
                                 logo=A.logo if A.logo and os.path.exists(A.logo) else None,
                                 accent=A.color if A.color.lower() != "#f6e27f" else None, seed_text=os.path.basename(A.src) + str(idx), log=log,
                                 brolls=_br, bilder=_bi)
            if _r:
                STATS["premium2"] = _r[1]
                STATS["caption_words"] = max(STATS.get("caption_words", 0), sum(1 for w_ in words_out if norm(w_[0])))  # Untertitel zeichnet der Baukasten
                # Hintergrundmusik (10.10.): wie im alten Weg – gleichmäßig leise, duckt unter die Stimme, weich rein/raus.
                # Scheitert das Mischen, bleibt das Video ohne Musik (nie deswegen den ganzen Auftrag verlieren).
                try:
                    if kept and idx not in MUSIC_PICK:
                        MUSIC_PICK[idx] = pick_music(kept)
                    _mu = MUSIC_PICK.get(idx)
                    if _mu:
                        _tot = float(probe_dur(out))
                        _tmp = out[:-4] + "-musik.mp4"
                        run(["ffmpeg", "-v", "error", "-y", "-i", out, "-stream_loop", "-1", "-i", _mu, "-filter_complex",
                             f"[0:a]asplit=2[vo][vk];[1:a]aresample=48000,atrim=0:{_tot:.3f},asetpts=PTS-STARTPTS,loudnorm=I=-24:TP=-4:LRA=9,"
                             f"afade=t=in:d=0.4,afade=t=out:st={max(0.0, _tot - 1.2):.3f}:d=1.2[mraw];"
                             "[mraw][vk]sidechaincompress=threshold=0.02:ratio=8:attack=30:release=450:makeup=1[mduck];"
                             "[vo][mduck]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.93:level=0[am]",
                             "-map", "0:v", "-map", "[am]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", _tmp])
                        if os.path.exists(_tmp) and os.path.getsize(_tmp) > 100000:
                            os.replace(_tmp, out)
                            STATS["premium2_musik"] = os.path.basename(_mu)
                except Exception as _em:  # noqa: BLE001
                    log(f"Premium: Musik nicht gemischt ({_em})")
                return out
            log("Premium-Grafiken (neu) nicht möglich – zeichne wie bisher")
        except Exception as _ep:  # noqa: BLE001
            log(f"Premium-Grafiken (neu) Fehler: {_ep} – zeichne wie bisher")
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
    CUT_TIMES.clear()
    _t = 0.0
    for _rs, _re in (ranges or [])[:-1]:
        _t += olen(_rs, _re)
        CUT_TIMES.append(round(_t, 3))
    # Ein Zwischenbild (B-Roll) ist auch ein Bildwechsel (Testlauf 12.10., Malo's: Karte „Capri-Sonne“ stand noch über dem
    # Burger-Zwischenbild, während schon „bester Burger in Mannheim“ gesagt wurde). Karten enden, wenn es kommt oder geht.
    for _b in (PLAN or {}).get("broll") or []:
        try:
            _tb = out_at(_b["t_src"])
        except Exception:  # noqa: BLE001
            _tb = None
        if _tb is not None:
            CUT_TIMES.extend([round(_tb, 3), round(_tb + min(float(_b.get("dur", 2.4)), 3.0), 3)])
    CUT_TIMES.sort()
    if not LOUD and ranges and kept:
        try:
            clean_extras(kept)
        except Exception as _ec:  # noqa: BLE001
            PREMX.clear()
            log(f"Clean-Marken nicht möglich ({_ec})")
    if LOUD:
        pev = premium_events(pfx, words_out, title, voll=PREM)
    else:
        try:   # Clean darf an seinen Zusatz-Marken nie scheitern
            pev = premium_events([], words_out, title, voll=False, ruhig=True)
        except Exception as _ec:  # noqa: BLE001
            log(f"Clean-Marken ausgelassen ({_ec})")
            CARDS.clear()
            pev = []
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
            _bf = b["file"]
            if RESOLVE:  # Zwischenbilder bekommen in Resolve dieselbe Art Korrektur wie der Schnitt (sonst stechen sie roh heraus)
                _bg = os.path.join(A.out, f"broll-{idx}-{k}.mp4")
                try:
                    if not os.path.exists(_bg):
                        RESOLVE.grade(_bf, _bg, A.style)
                    _bf = _bg
                except Exception as _e:
                    log(f"Resolve-Farbe Zwischenbild {k}: {_e} → bleibt wie es ist")
            inputs += ["-i", _bf]
            # Inhaber 07.10.: „Man muss den Unterschied zwischen den Stilen spüren – wie etwas ins Bild kommt, wie die B-Rolls
            # reinkommen.“ Jeder Stil hat seine eigene Art, ein Zwischenbild zu bringen (alle drei einzeln mit ffmpeg geprüft):
            #   Clean   – blendet weich ein und aus (ruhig)
            #   Viral   – schiebt schnell von rechts herein (dazu Whoosh)
            #   Premium – zoomt sich weich auf die volle Größe ein (Kino-Gefühl)
            if PREM:
                fc.append(f"[{ni}:v]trim=0:{d:.3f},setpts=PTS-STARTPTS,scale=w='2*trunc(540*(1+0.10*pow(1-min(t/0.4,1),2)))':h=-2:eval=frame,"
                          f"format=rgba,fade=t=in:st=0:d=0.12:alpha=1,setpts=PTS+{t0:.3f}/TB[br{k}]")
                fc.append(f"[{base}][br{k}]overlay=x='(W-w)/2':y='(H-h)/2':eof_action=pass[bv{k}]")
            elif LOUD:
                fc.append(f"[{ni}:v]trim=0:{d:.3f},setpts=PTS-STARTPTS+{t0:.3f}/TB[br{k}]")
                fc.append(f"[{base}][br{k}]overlay=x='W*pow(1-clip((t-{t0:.3f})/0.16,0,1),3)':y=0:eof_action=pass[bv{k}]")
                plan_snd.append((max(0.0, t0 - 0.05), "whoosh", 0.2))
            else:
                fc.append(f"[{ni}:v]trim=0:{d:.3f},setpts=PTS-STARTPTS,format=rgba,fade=t=in:st=0:d=0.15:alpha=1,"
                          f"fade=t=out:st={max(0.2, d - 0.15):.3f}:d=0.15:alpha=1,setpts=PTS+{t0:.3f}/TB[br{k}]")
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
    BELEGT[:] = [(210, c["y"], 870, c["y"] + 130, c["t"], c["bis"] + 0.3, "Karte") for c in CARDS]   # Karten haben Vorrang
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
                    and not belegt(x, y, x + LOGO_PX, y + LOGO_PX, t0, t0 + 2.6)
                    and y + LOGO_PX <= SAFE_B]
            if not frei:
                continue
            x, y = frei[0]
            BELEGT.append((x, y, x + LOGO_PX, y + LOGO_PX, t0, t0 + 2.6, "Logo"))
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
            _sw, _sh = _Img.open(png).size
            _was = belegt(sx, sy, sx + _sw * f_, sy + _sh * f_, t0, t0 + b["dur"] + 0.4)
            if _was:
                log(f"Sticker {k + 1} weggelassen – dort steht gerade schon etwas ({_was})")
                continue
            BELEGT.append((sx, sy, sx + _sw * f_, sy + _sh * f_, t0, t0 + b["dur"] + 0.4, "Sticker"))
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
        if VIRAL:   # steht das Stichwort schon als Karte (mit Emoji) im Bild, kommt das Emoji nicht zusätzlich einzeln
            emo = [f for f in emo if not any(abs(c["t"] - f["t"]) < 1.0 for c in CARDS)]
        STATS.update(sfx=len(snd), fx=[(round(f["t"], 1), f["emoji"], f["sfx"]) for f in fx])
        # Emojis UNTER den Untertiteln, dezent (140 px), gleiten abwechselnd von links/rechts/unten/oben herein und wieder weg
        # Regel 112: jedes Emoji bekommt seinen Platz aus der gemeinsamen Platzvergabe (nie auf Untertitel, Titel, Logo, Gesicht,
        # nie unter den Knöpfen der Apps). Ist nirgends frei → lieber kein Emoji.
        spots, _r = {}, EMOJI_PX // 2
        for f in emo:
            sp_ = emoji_spot(f["t"], f["t"] + 1.7)
            if sp_ and belegt(sp_[0] - _r, sp_[1] - _r, sp_[0] + _r, sp_[1] + _r, f["t"], f["t"] + 1.7):
                sp_ = None   # dort steht gerade eine Karte, ein Sticker oder das Logo → lieber kein Emoji
            if sp_:
                BELEGT.append((sp_[0] - _r, sp_[1] - _r, sp_[0] + _r, sp_[1] + _r, f["t"], f["t"] + 1.7, "Emoji"))
            spots[id(f)] = sp_
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
    if CARDS:  # Stichwort-Karten: gleiten beim gesprochenen Wort von unten herein, Stapel blendet gemeinsam aus
        for k, c in enumerate(CARDS):
            try:
                png, pad = card_png(c["text"], c.get("emoji"), os.path.join(A.out, f"karte-{idx}-{k}.png"), nr=c.get("nr"), art=c.get("art"))
            except Exception as e:  # noqa: BLE001
                log(f"Karte „{c['text']}“ ausgelassen ({e})")
                continue
            d = max(1.0, c["bis"] - c["t"])
            inputs += ["-loop", "1", "-t", f"{d + 0.3:.2f}", "-i", png]
            dreh = -0.10 if k % 2 == 0 else 0.10  # Karten drehen sich abwechselnd von links/rechts leicht herein
            fc.append(f"[{ni}:v]format=rgba,rotate='{dreh}*pow(1-clip(t/0.32,0,1),2)':c=none:ow=iw:oh=ih,fade=t=in:st=0:d=0.2:alpha=1,"
                      f"fade=t=out:st={d:.2f}:d=0.28:alpha=1,setpts=PTS+{c['t']:.3f}/TB[ka{k}]")
            fc.append(f"[{vin}][ka{k}]overlay=x=(W-w)/2:y='{c['y'] - pad}+46*pow(1-clip((t-{c['t']:.3f})/0.28,0,1),3)':eof_action=pass[kv{k}]")
            vin, ni = f"kv{k}", ni + 1
        STATS["premium_karten"] = [(round(c["t"], 1), c["text"], c.get("art") or (f"Nr. {c['nr']}" if c.get("nr") else "")) for c in CARDS]
    if PREM and ranges and len(ranges) >= 3:
        # Übergänge mit Abwechslung (Inhaber 07.10.: „springende Zooms, Lichtwisch und harte Schnitte – wechsel ab“): Die
        # Zoom-Stufen wechseln schon an jedem Schnitt; an jedem dritten Schnitt zieht zusätzlich ein weicher Lichtstreifen
        # durchs Bild (0,26 s, mit Whoosh), die übrigen bleiben harte Schnitte.
        try:
            from PIL import Image as _LI, ImageDraw as _LD, ImageFilter as _LF
            lw = os.path.join(A.out, f"lichtwisch-{idx}.png")
            im = _LI.new("RGBA", (1080, 1920), (0, 0, 0, 0))
            _LD.Draw(im).polygon([(330, 0), (760, 0), (520, 1920), (90, 1920)], fill=(255, 250, 235, 105))
            im.filter(_LF.GaussianBlur(70)).save(lw)
            tc, n_lw = 0.0, 0
            for k, (rs, re_) in enumerate(ranges[:-1]):
                tc += olen(rs, re_)
                if k % 3 != 1 or n_lw >= 6 or tc < 3.5:
                    continue
                inputs += ["-loop", "1", "-t", "0.30", "-i", lw]
                fc.append(f"[{ni}:v]format=rgba,setpts=PTS+{tc - 0.10:.3f}/TB[lw{k}]")
                fc.append(f"[{vin}][lw{k}]overlay=x='-W+2*W*(t-{tc - 0.10:.3f})/0.26':y=0:eof_action=pass[lv{k}]")
                vin, ni = f"lv{k}", ni + 1
                snd.append((max(0.0, tc - 0.08), "whoosh", 0.16))
                n_lw += 1
            STATS["lichtwisch"] = n_lw
        except Exception as e:  # noqa: BLE001
            log(f"Lichtwisch ausgelassen ({e})")
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
        # „doppelt“ heißt: EIN anderer behaltener Satz sagt dasselbe. Über alle Sätze zusammengezählt galt „Echtes Thai scharf
        # zu essen“ als doppelt (Thai, scharf, essen kommen verstreut vor) und flog im Viral-Test 08.10. dreimal raus.
        deckung = max((len(a_ & inhalt(sents[j])) / len(a_) for j in keep), default=0.0) if a_ else 1.0
        panne = re.search(r"verbrannt|angebrannt|ist falsch|falsch gemacht|oh no|nochmal|warte", " ".join(w["t"] for w in sents[k]).lower())
        if a_ and deckung < 0.6 and not panne:
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
    if abs(float(info["format"]["duration"]) - secs - (STATS.get("premium2") or {}).get("abspann_s", 0.0)) > 1.0:
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
ABGELEHNT = []   # Clips, die die Untergrenze der Schluss-Abnahme nicht geschafft haben (werden nicht ausgeliefert)


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


ANLAUF = {"so", "ja", "okay", "ok", "also", "gut", "genau", "alles", "klar", "hm", "mhm", "jo", "jetzt", "bitte", "action", "und"}


def kurz_anfang(ranges, kept):
    """Testlauf 12./13.10. (Cheesecake Erdbeerbecher): Das Video begann mit dem einzelnen Wort „More.“ – der Rest eines Satzes,
    dessen Anfang nicht im Schnitt war. Ein Video beginnt nie mit einem Satzrest: Sind die ersten ein, zwei Wörter ein eigener
    „Satz“, der mit Punkt endet (kein Ausruf, keine Frage, keine Begrüßung), fällt er weg und das Video beginnt beim nächsten Satz."""
    if len(kept) < 10:
        return ranges, kept
    k = next((i for i, w in enumerate(kept[:3]) if re.search(r"[.!?…]\W*$", w["t"].strip())), None)
    if k is None or k > 1 or not kept[k]["t"].strip().endswith(".") \
            or any(norm(w["t"]) in {"hallo", "hi", "hey", "servus", "moin", "leute", "achtung", "stopp", "stop", "danke", "willkommen", "ja", "nein", "okay"} for w in kept[:k + 1]):
        return ranges, kept
    nxt = kept[k + 1]
    neu = [[max(a_, nxt["s"] - 0.06), e_] for a_, e_ in ranges if e_ > nxt["s"] + 0.05]
    if not neu or sum(e_ - a_ for a_, e_ in neu) < 3.0:
        return ranges, kept
    log(f"Satzrest am Videoanfang entfernt („{' '.join(w['t'] for w in kept[:k + 1])}“) – das Video beginnt mit „{nxt['t']} …“")
    STATS["satzrest_anfang"] = " ".join(w["t"] for w in kept[:k + 1])
    return neu, kept[k + 1:]


def schluss_pruefung(ranges, kept):
    """Inhaber 13.10.: „Das Video endet viel zu spät oder zu früh – das musst du selber einschätzen können.“ Eine eigene Prüfung
    nur für diese Frage: Nach welchem Satz ist das Video zu Ende? Was nach dem eigentlichen Schlusspunkt (Pointe, Auflösung,
    Aufruf, Verabschiedung) noch kommt – Nachgeplänkel, ein zweiter Abschied, ein angehängter Satz zu etwas anderem –, fällt weg.
    Gekürzt werden höchstens die letzten 3 Sätze und höchstens 30 % des Videos; der Anfang wird hier nie angefasst."""
    if len(kept) < 12:
        return ranges, kept
    saetze, cur = [], []
    for w in kept:
        cur.append(w)
        if SENT_END.search(w["t"].strip()):
            saetze.append(cur)
            cur = []
    if cur:
        saetze.append(cur)
    if len(saetze) < 3:
        return ranges, kept
    res = gemini(
        "Du bist ein erfahrener Cutter für TikTok/Reels. Unten steht der gesprochene Text eines fertig geschnittenen Kurzvideos, "
        "Satz für Satz nummeriert. Deine einzige Aufgabe: Nach welchem Satz ist das Video ZU ENDE? Ein gutes Ende ist die Pointe, "
        "die Auflösung, das vollständige Angebot, der Aufruf („Kommt vorbei“) oder EINE kurze Verabschiedung. Danach kommt nichts "
        "mehr. Zu spät endet ein Video, wenn nach diesem Schlusspunkt noch etwas folgt: Nachgeplänkel („Okay.“ – „Ja.“), ein "
        "zweiter und dritter Abschied, eine neue Begrüßung, ein Satz zu einem anderen Thema oder Produkt, ein Satz an den "
        "Kameramann. Gib die Nummer des Satzes an, mit dem das Video enden soll. Ist das jetzige Ende richtig, gib die Nummer des "
        "letzten Satzes an. Kürze nur, wenn du sicher bist – ein Aufruf oder Angebot am Schluss gehört VOLLSTÄNDIG dazu. "
        "Zusätzlich: „offen“ = true, wenn der letzte Satz mitten im Gedanken abbricht (dann fehlt etwas, das du nicht ergänzen kannst).",
        "\n".join(f"{k + 1}: {' '.join(w['t'] for w in st)}" for k, st in enumerate(saetze)),
        {"type": "object", "properties": {"ende_nach": {"type": "integer"}, "offen": {"type": "boolean"}, "grund": {"type": "string"}},
         "required": ["ende_nach", "grund"]}, deep=True)
    if not res:
        return ranges, kept
    STATS["schluss"] = {"saetze": len(saetze), "ende_nach": res.get("ende_nach"), "offen": bool(res.get("offen")), "grund": str(res.get("grund") or "")[:160]}
    n_ = int(res.get("ende_nach") or len(saetze))
    if res.get("offen"):
        log(f"Schluss-Prüfung: der letzte Satz wirkt abgebrochen – {str(res.get('grund') or '')[:140]}")
    if not (max(2, len(saetze) - 3) <= n_ < len(saetze)):
        if n_ == len(saetze):
            log("Schluss-Prüfung: das Video endet an der richtigen Stelle")
        return ranges, kept
    bleibt = [w for st in saetze[:n_] for w in st]
    if len(bleibt) < 0.7 * len(kept):
        log(f"Schluss-Prüfung: Vorschlag (Ende nach Satz {n_}) würde zu viel wegnehmen – bleibt")
        return ranges, kept
    ende = bleibt[-1]["e"] + 0.3
    neu = [[a_, min(e_, ende)] for a_, e_ in ranges if a_ < ende - 0.1]
    if not neu:
        return ranges, kept
    log(f"Schluss-Prüfung: Video endet jetzt nach „{' '.join(w['t'] for w in saetze[n_ - 1])[-60:]}“ – weg: „{' '.join(w['t'] for st in saetze[n_:] for w in st)[:90]}“ ({str(res.get('grund') or '')[:120]})")
    STATS["schluss"]["gekuerzt"] = " ".join(w["t"] for st in saetze[n_:] for w in st)[:200]
    return neu, bleibt


def gruss_am_ende(ranges, kept):
    """Testlauf 13.10. (Cheesecake-Umfrage „Kommentare“): Das Video endete mit „… Okay, danke. Tschüss. Hallo.“ – das „Hallo“
    gehört schon zum nächsten Gespräch. Ein Video endet nie mit einer Begrüßung: Besteht der letzte „Satz“ nur aus ein, zwei
    Begrüßungswörtern und kam davor schon etwas, fällt er weg."""
    if len(kept) < 10:
        return ranges, kept
    k = len(kept) - 1
    while k > 0 and len(kept) - k < 3 and not re.search(r"[.!?…]\W*$", kept[k - 1]["t"].strip()):
        k -= 1
    letzter = kept[k:]
    if not (1 <= len(letzter) <= 2) or k == 0 or not re.search(r"[.!?…]\W*$", kept[k - 1]["t"].strip()) \
            or not all(norm(w["t"]) in {"hallo", "hi", "hey", "entschuldigung", "servus", "moin", "tag"} for w in letzter):
        return ranges, kept
    ende = kept[k - 1]["e"] + 0.25
    neu = [[a_, min(e_, ende)] for a_, e_ in ranges if a_ < ende - 0.1]
    if not neu:
        return ranges, kept
    log(f"Begrüßung am Videoende entfernt („{' '.join(w['t'] for w in letzter)}“) – das Video endet mit „{kept[k - 1]['t']}“")
    STATS["gruss_ende_raus"] = " ".join(w["t"] for w in letzter)
    return neu, kept[:k]


NACHLAUF = ANLAUF | {"nochmal", "noch", "mal", "einmal", "cut", "danke", "super", "perfekt", "passt", "top", "stopp", "stop",
                     "war", "das", "wars", "gut", "nee", "ne", "nein", "sorry", "moment", "warte"} - {"und", "jetzt"}


def take_anlauf(ranges, kept):
    """Inhaber 12.10. (Cheesecake „unter 10 Euro“, Sekunde 6): „Er sagt ‚So.‘ – das gehört nicht ins Video. Erst ab ‚Ihr
    bekommt‘.“ Am Anfang einer neuen Aufnahme sagt der Sprecher oft ein Anlauf-Wort („So.“, „Ja.“, „Okay.“), bevor der Satz
    beginnt. Die erste Erkennung überhört das gern (hier steckte das „So“ im überlangen Wort „Ihr“, 0,5 s) – dann bleibt es im
    Ton. Deshalb wird der Anfang JEDER Aufnahme (Clip-Wechsel im Schnittplan) noch einmal einzeln abgehört: Steht vor dem ersten
    Wort des Satzes ein Anlauf-Wort, beginnt der Schnitt erst danach – an der leisesten Stelle zwischen beiden."""
    js = sorted((PLAN or {}).get("joins") or [])
    if not js:
        return ranges
    log(f"Anlauf-Prüfung: Clip-Wechsel bei {[round(j, 2) for j in js]}, Abschnitte ab {[round(r[0], 2) for r in ranges]}")
    snip, n = os.path.join(A.out, "anlauf.wav"), 0
    # „Anfang einer Aufnahme“ = der erste Abschnitt nach jedem Clip-Wechsel (er beginnt oft erst 0,3–0,8 s dahinter, weil
    # Regel 109 die stille Vorlaufzeit schon wegnimmt – erster Versuch mit 0,3 s Toleranz griff deshalb nie).
    erste = {id(next((r_ for r_ in ranges if r_[0] >= j - 0.05), None)) for j in js}
    for r in ranges:
        if id(r) not in erste or r[1] - r[0] < 1.0:
            continue
        w0 = next((w for w in kept if w["e"] > r[0] + 0.02 and w["s"] < r[1]), None)
        if not w0 or norm(w0["t"]) in ANLAUF:
            continue
        try:
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{r[0]:.3f}", "-t", f"{min(3.0, r[1] - r[0]):.3f}", "-i", wav_asr, snip])
            tr_ = mlx_whisper.transcribe(snip, **ASR, temperature=0.0, initial_prompt="So. Ja. Okay. Also.")
        except Exception as e:  # noqa: BLE001
            log(f"Anlauf-Prüfung übersprungen ({e})")
            break
        h = [(norm(x["word"]), float(x["start"]), float(x["end"])) for sg in tr_["segments"] for x in sg.get("words", []) if norm(x["word"])]
        log(f"  Aufnahme-Anfang {r[0]:.2f} s: gehört „{' '.join(x[0] for x in h[:4])}“, Text beginnt mit „{w0['t']}“")
        k = 0
        while k < len(h) and k < 2 and h[k][0] in ANLAUF and h[k][0] != norm(w0["t"]):
            k += 1
        if not k or k >= len(h) or h[k][0][:4] != norm(w0["t"])[:4]:
            continue   # kein Anlauf-Wort – oder der Satz danach passt nicht zum Text (dann lieber nichts anfassen)
        i0, i1 = int((r[0] + h[k - 1][1] + 0.05) / 0.05), int((r[0] + h[k][1] + 0.1) / 0.05) + 1
        seg = LV[max(0, i0):max(i0 + 1, min(len(LV), i1))]
        neu = (max(0, i0) + seg.index(min(seg))) * 0.05 + 0.025 if seg else r[0] + h[k][1] - 0.06
        neu = min(neu, r[0] + h[k][1] + 0.05)
        if 0.08 < neu - r[0] <= 1.6 and r[1] - neu >= 0.5:
            log(f"Anlauf-Wort am Aufnahme-Anfang („{' '.join(x[0] for x in h[:k])}“ vor „{w0['t']}“) – Schnitt beginnt {neu - r[0]:.2f} s später")
            r[0] = neu
            n += 1
    # Dasselbe am ENDE jeder Aufnahme („nicht zu früh, nicht zu spät“): Hört man nach dem letzten Wort des Satzes noch etwas
    # Gesprochenes, das nicht im Text steht („Ja.“, „Okay, nochmal“, Regie), endet der Schnitt davor – an der leisesten Stelle
    # dazwischen. Nur deutlich Gehörtes (Sicherheit ≥ 0,5), höchstens 2 s; stumme Handlung nach dem Satz bleibt unangetastet.
    m = 0
    letzte = {id(ranges[i - 1]) for i, r_ in enumerate(ranges) if i and id(r_) in erste} | {id(ranges[-1])}
    for r in ranges:
        if id(r) not in letzte or r[1] - r[0] < 1.0:
            continue
        w1 = next((w for w in reversed(kept) if w["s"] < r[1] - 0.02 and w["e"] > r[0]), None)
        if not w1 or r[1] - w1["e"] < 0.25:
            continue
        a0 = max(r[0], w1["s"] - 1.2)
        try:
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a0:.3f}", "-t", f"{r[1] - a0:.3f}", "-i", wav_asr, snip])
            tr_ = mlx_whisper.transcribe(snip, **ASR, temperature=0.0)
        except Exception as e:  # noqa: BLE001
            log(f"Nachlauf-Prüfung übersprungen ({e})")
            break
        h = [(norm(x["word"]), float(x["start"]), float(x["end"]), float(x.get("probability", 1.0)))
             for sg in tr_["segments"] for x in sg.get("words", []) if norm(x["word"])]
        j = next((k for k in range(len(h) - 1, -1, -1) if h[k][0][:4] == norm(w1["t"])[:4]), None)
        nach = [x for x in h[j + 1:] if x[3] >= 0.5] if j is not None else []
        # Nur typische Regie-/Abschluss-Wörter zählen als Nachlauf. Ein anderes gehörtes Wort kann das echte Satzende sein,
        # das die erste Erkennung überhört hat – das darf nie weggeschnitten werden (dafür gibt es das Nachtragen in align).
        if not nach or nach[0][1] < h[j][2] - 0.02 or not all(x[0] in NACHLAUF for x in nach):
            continue
        i0, i1 = int((a0 + h[j][2] + 0.04) / 0.05), int((a0 + nach[0][1] + 0.04) / 0.05) + 1
        seg = LV[max(0, i0):max(i0 + 1, min(len(LV), i1))]
        neu = (max(0, i0) + seg.index(min(seg))) * 0.05 + 0.025 if seg else a0 + h[j][2] + 0.1
        neu = max(neu, a0 + h[j][2] + 0.06, w1["e"] + 0.04)
        if 0.12 <= r[1] - neu <= 2.0 and neu - r[0] >= 0.5:
            log(f"Nachlauf am Aufnahme-Ende („{' '.join(x[0] for x in nach[:3])}“ nach „{w1['t']}“) – Schnitt endet {r[1] - neu:.2f} s früher")
            r[1] = neu
            m += 1
    if os.path.exists(snip):
        os.remove(snip)
    if n:
        STATS["anlauf_raus"] = n
    if m:
        STATS["nachlauf_raus"] = m
    return ranges


def trim_joins(ranges, kept, joins):
    """Regel 109 (Inhaber 06.10.: vor „Ihr bekommt bei uns“ hört man ein „Ja“ vom Kameramann; bei der Junior Box setzt das
    Bild erst ein, wenn er schon redet). An einer Clip-Grenze gehören Nachlauf des einen und Vorlauf des nächsten Clips nicht
    zusammen. Der Abschnitt davor endet in der ersten Ruhe nach seinem letzten Wort (fremde Laute danach fallen weg), der
    Abschnitt danach beginnt in der letzten Ruhe vor seinem ersten Wort (man sieht die Person ansetzen, hört aber nichts Fremdes)."""
    out, n_cut = [], 0.0
    # Sicherung: gekürzt wird nur an einer ECHTEN Ruhe-Stelle (mind. 12 dB unter Sprachlautstärke). Liegt zwischen dem Wort
    # und dem Rand durchgehend Schall, bleibt alles stehen – es könnte ein Wortanfang sein, den die Erkennung verschluckt hat.
    _lvj = sorted(v for v in LV if v > -95.0) or sorted(LV)
    laut = _lvj[int(len(_lvj) * 0.9)] if _lvj else 0.0
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
            # Inhaber 07.10. (Premium-Test): „Kurz bevor er redet, nimmt er Luft – das hört man, und einen Moment passiert
            # nichts. Das muss direkt nacheinander kommen.“ Atmen ist kein „fremdes Geräusch“ und keine Stille; es liegt aber
            # deutlich unter der Sprachlautstärke. Der Abschnitt beginnt deshalb am echten Einsatz der Stimme: vom erkannten
            # Wortanfang rückwärts, solange es sprachlaut ist – davor (Luftholen, Lippengeräusch, Leerlauf) wird abgeschnitten.
            if first is not None and first - a > 0.12:
                i0 = int(first / 0.05)
                while i0 > int(a / 0.05) and (lv(i0 * 0.05 - 0.025) >= laut - 16 or lv(i0 * 0.05 - 0.075) >= laut - 16):
                    i0 -= 1
                a = max(a, min(first - 0.04, i0 * 0.05 - 0.06))
        if b - a > 0.08:
            out.append([a, b])
            n_cut += (a - a0) + (b0 - b)
    STATS["clipgrenzen_gekuerzt_s"] = round(n_cut, 2)
    return out


def sprachkanten(ranges, kept):
    """Inhaber 07.10. (Golden Thai): „…dass du das über Papaya weißt“ – das „weißt“ wurde am Ende angeschnitten; und vor
    einem Satz sieht man ihn tief einatmen und aufs Handy (Skript) schauen. Beides ist dieselbe Ursache: Die Zeiten der
    Worterkennung stimmen an den Rändern nicht genau. An jedem echten Schnitt wird deshalb der Pegel befragt:
      Ende   – läuft die Stimme über das erkannte Wortende hinaus, bleibt der Abschnitt bis zum echten Ende der Stimme stehen
               (höchstens 0,35 s; nur wenn danach wirklich Ruhe kommt – sonst wäre es schon das nächste, gestrichene Wort).
      Anfang – was vor dem Einsatz der Stimme liegt (Luftholen, Blick zur Seite, Leerlauf), wird abgeschnitten."""
    if not LV:
        return ranges
    # Malo's-Test 09.10. („an der Theke ist er noch nicht bereit zu reden“): Die stumme Bildfolge ist digitale Stille (−120 dB).
    # Sie zog den Ruhe-Wert so tief, dass sogar das Raumrauschen (−70 dB) als „Stimme“ galt – am Satzanfang wurde nichts mehr
    # gekürzt. Digitale Stille zählt deshalb bei der Bestimmung von Ruhe und Sprachlautstärke nicht mit.
    _lvx = sorted(v for v in LV if v > -95.0) or sorted(LV)
    laut = _lvx[int(len(_lvx) * 0.9)]
    ruhe = _lvx[int(len(_lvx) * 0.2)]
    # Schwelle „hier spricht jemand“: knapp unter der Mitte zwischen Grundrauschen und Sprachlautstärke. Eine feste Zahl
    # unter dem Sprachpegel taugt nicht – bei einer sehr leisen Aufnahme (Golden Thai: Sprache −41 dB, Rauschen −64 dB)
    # lag das Atmen sonst schon über der Schwelle.
    thr = ruhe + 0.45 * (laut - ruhe)
    lv = lambda t: LV[min(len(LV) - 1, max(0, int(t / 0.05)))]
    # In einer Zubereitung passiert die Handlung NACH der Ansage (Regel 107) – dort bleibt der Nachlauf stehen
    _handlung_bleibt = bool(re.search(r"zubereit|anleitung|tutorial|rezept|behind|so wird|so entsteht",
                                      str((PLAN or {}).get("format") or (STATS.get("story_format") or {}).get("format") or "").lower()))
    # Inhaber 08.10. (Ordner-Tests Golden Thai): „Kurz bevor er sagt ‚Willst du eine Mittagspause‘, hängt das ein bisschen“,
    # „nach ‚Restaurants‘ hätte man kürzer cutten können“. Gemessen: 0,4–0,5 s Leerlauf MITTEN in einem Abschnitt. Ursache:
    # Die Erkennung lässt das nächste Wort zu früh beginnen („Die“ ab 10,34 s, gesprochen ab 10,68 s) – für den Schnitt sah es
    # aus, als ginge die Sprache durch. Jetzt entscheidet auch hier der Pegel: Liegt innerhalb eines Abschnitts 0,4 s oder
    # länger nur Ruhe/Atmen (kein leises Wort darin), bleiben davon 0,22 s stehen – der Rest fällt weg.
    _zeigen = [(float(z[0]), float(z[1])) for z in ((PLAN or {}).get("zeigen") or []) if isinstance(z, (list, tuple)) and len(z) >= 2]
    _joins = list((PLAN or {}).get("joins") or [])
    # Kleine Schnitte (< 0,25 s, kein gestrichenes Wort, kein Clip-Wechsel), die der Grobschnitt schon gesetzt hat, werden zuerst
    # geschlossen – sonst sieht die Leerlauf-Prüfung die Pause nur in zwei Hälften und lässt einen Laut-Fetzen darin stehen.
    _ks = {(round(w["s"], 2), round(w["e"], 2)) for w in kept}
    _weg = [w for w in WORDS if (round(w["s"], 2), round(w["e"], 2)) not in _ks]
    zu = []
    for a, b in ranges:
        if zu and 0.02 <= a - zu[-1][1] < 0.25 and not any(abs(a - j) < 0.2 for j in _joins) \
                and not any(w["s"] < a and w["e"] > zu[-1][1] for w in _weg):
            zu[-1][1] = b
        else:
            zu.append([a, b])
    ranges = zu
    neu, leer = [], 0.0
    for a, b in ranges:
        cur, t = a, a + 0.15
        while not _handlung_bleibt and t < b - 0.15:
            if lv(t) >= thr:
                t += 0.05
                continue
            q0, fetzen = t, None
            # Inhaber 08.10. (Premium, „am Ende hört man zweimal ‚mit‘“): In der Pause zwischen „… Mit“ und „Tofu“ steckt ein
            # einzelner leiser Laut von 0,05 s (angesetztes Wort). Ein solcher Fetzen – eine einzige Messstelle, mindestens
            # 8 dB unter Sprachlautstärke, davor und danach Ruhe – zählt zur Pause und wird mit ihr herausgeschnitten.
            while t < b - 0.15 and (lv(t) < thr or (lv(t) < laut - 8 and lv(t - 0.05) < thr and lv(t + 0.05) < thr)):
                if lv(t) >= thr and fetzen is None:
                    fetzen = t
                t += 0.05
            q1 = t
            if q1 >= b - 0.15 or q1 - q0 < 0.4:
                continue
            if any(w["s"] >= q0 - 0.02 and w["e"] <= q1 + 0.02 for w in kept) or any(z0 < q1 and z1 > q0 for z0, z1 in _zeigen):
                continue
            # Vorlauf 0,16 s (vorher 0,10): „Echtes Thai scharf“ kam als „Ich das Thai scharf“ heraus – der weiche Vokal am
            # Wortanfang liegt noch unter der Schwelle, der Schnitt saß 0,04 s davor und die Blende nahm den Anlaut weg.
            c0, c1 = q0 + 0.12, q1 - 0.16
            if fetzen is not None:
                # Nachtrag 08.10. abends (Inhaber: „man hört trotzdem zweimal ‚mit‘, und ‚Tofu‘ kommt zu schnell“): Der Fetzen
                # ist 0,1 s lang und beginnt schon in der ersten ruhigen Messstelle – der Schnitt setzt deshalb direkt am Ende
                # des Wortes davor an (Anfang der Ruhe) und lässt vor dem nächsten Wort eine hörbare Pause (0,16 s) stehen.
                c0 = int(q0 / 0.05 + 1e-6) * 0.05
                c1 = q1 - 0.16
            if c1 - c0 >= 0.15 and c0 - cur >= 0.3 and b - c1 >= 0.3:
                neu.append([cur, c0])
                leer += c1 - c0
                cur = c1
        neu.append([cur, b])
    _geteilt = {round(r[0], 2) for r in neu} - {round(r[0], 2) for r in ranges}
    _kante = {round(r[1], 2) for r in neu} - {round(r[1], 2) for r in ranges}   # Enden, die die Leerlauf-Regel gesetzt hat
    ranges = neu
    out, verl, gek = [], 0.0, 0.0
    for k, (a, b) in enumerate(ranges):
        # Zuordnung über die Wortmitte: Die Erkennung lässt ein Wort nach einer Pause oft zu früh beginnen („Ihr“ ab 12,08 s,
        # gesprochen erst ab 12,5 s). Über den Wortanfang gezählt, galt es als letztes Wort des Abschnitts DAVOR – und dessen
        # Nachlauf mit „so“ und Kopfdrehen blieb stehen (Premium-Test 07.10.).
        ws = [w for w in kept if a - 0.05 <= (w["s"] + w["e"]) / 2 < b]
        b_alt = b
        prev_b = out[-1][1] if out else None   # das (evtl. schon gekürzte) Ende des Abschnitts davor
        next_a = ranges[k + 1][0] if k + 1 < len(ranges) else None
        if ws and round(b, 2) not in _kante and (next_a is None or next_a - b >= 0.3) and lv(b) >= thr:
            lim = min(b + 0.35, (next_a - 0.05) if next_a is not None else DUR)
            t = b
            while t < lim and lv(t) >= thr:
                t += 0.05
            if t < lim:                      # die Stimme endet wirklich innerhalb der Frist → bis dorthin stehen lassen
                verl += t + 0.04 - b
                b = t + 0.04
        if ws and next_a is not None and not _handlung_bleibt:  # auch wenn der nächste Abschnitt direkt anschließt (Clip-Wechsel)
            # Inhaber 07.10. (Premium): Nach „… Toppings nach Wahl“ sagt er noch „so“ und dreht den Kopf – „da muss direkt
            # der Cut kommen“. Vor einem Schnitt endet der Abschnitt deshalb dort, wo die Stimme des letzten behaltenen Wortes
            # verklingt (+0,15 s) – was danach kommt (Laut, Blick, Leerlauf), gehört nicht mehr dazu.
            # Nachtrag 08.10.: Eine Senke von 0,1 s mitten im letzten Wort („Thailan-ds“) ist noch nicht das Ende der Stimme –
            # sonst fehlt der Schlusslaut und es klingt wie ein Versprecher.
            t = max(a, ws[-1]["e"] - 0.1)
            while t < b and (lv(t) >= thr or lv(t + 0.05) >= thr or lv(t + 0.10) >= thr):
                t += 0.05
            if b - (t + 0.10) > 0.06:
                gek += b - (t + 0.10)
                b = t + 0.10
        # Auch an einem Clip-Wechsel (dort schließen die Abschnitte direkt aneinander an): Vor „Willst du …“ blieben 0,4 s
        # Atmen stehen, weil der Anfang nur nach einer Lücke geprüft wurde (Inhaber 08.10.).
        am_wechsel = any(abs(a - j) < 0.2 for j in _joins)
        if ws and round(a, 2) not in _geteilt and (prev_b is None or a - prev_b >= 0.3 or am_wechsel):
            lim = min(ws[0]["e"] - 0.12, a + 1.5)
            t = a
            while t < lim:
                if lv(t) < thr and lv(t + 0.05) < thr:
                    t += 0.05
                    continue
                # Inhaber 08.10. (Viral, „nach ‚Küche‘ hört man einen kleinen Versprecher“): ein kurzer Laut (Schmatzen, „ä“,
                # Ansatz) und danach wieder Ruhe, noch bevor das erste Wort wirklich beginnt – der Laut gehört nicht zum Satz.
                l1 = t
                while l1 < lim and (lv(l1) >= thr or lv(l1 + 0.05) >= thr):
                    l1 += 0.05
                q1 = l1
                while q1 < lim and lv(q1) < thr:
                    q1 += 0.05
                if l1 - t <= 0.3 and q1 - l1 >= 0.3:
                    t = l1
                    continue
                break
            if t - a >= 0.14:
                gek += t - 0.10 - a
                a = t - 0.10
        # Gegenstück zum Ende (Viral-Test 08.10.: „Echtes Thai scharf“ kam als „…chtes“ heraus – die Erkennung ließ das Wort erst
        # 0,18 s NACH dem lauten „E“ beginnen): Liegt direkt vor dem Abschnitt noch Stimme und davor Ruhe, beginnt der Abschnitt
        # am echten Einsatz (höchstens 0,35 s früher; nie in ein gestrichenes Wort oder den Abschnitt davor hinein).
        if ws and round(a, 2) not in _geteilt and lv(a - 0.05) >= thr:
            grenze = max((prev_b + 0.06) if prev_b is not None else 0.0, a - 0.35)
            t = a
            while t - 0.05 >= grenze and lv(t - 0.05) >= thr:
                t -= 0.05
            if t < a and t - 0.05 >= grenze and lv(t - 0.05) < thr and not any(w["s"] < a and w["e"] > t for w in _weg):
                verl += a - (t - 0.06)
                a = max(grenze, t - 0.06)
        elif ws and round(a, 2) not in _geteilt and (lv(a) >= thr or lv(a + 0.05) >= thr) and (prev_b is None or a - prev_b >= 0.15):
            # Die Stimme setzt praktisch AM Schnitt ein (0,03 s Vorlauf): Die Blende am Schnitt nimmt dann den Anlaut weg
            # („Echtes“ → „…chtes“). Davor ist Ruhe – also 0,08 s früher beginnen.
            neu_a = max((prev_b + 0.06) if prev_b is not None else 0.0, a - 0.08)
            if not any(w["s"] < a and w["e"] > neu_a for w in _weg):
                verl += a - neu_a
                a = neu_a
        if ws and next_a is None:
            # Inhaber 07.10. (Premium): „Am Schluss direkt cutten – nicht warten, bis die Kamera runtergeht.“ Das Video endet
            # 0,3 s nach dem echten Ende der Stimme (statt bis zu 1,3 s Nachlauf).
            # Lauf 10.10. (Malo's, drei Abnahmen in Folge: „der letzte Satz bricht mitten im Wort ab“): Der Ausklang von
            # „Capri-Sonne“ („…nne“, −50 dB) lag unter der Sprech-Schwelle und galt als Ruhe – das Video endete im Wort.
            # Für das Ende des Videos gilt deshalb eine tiefere Schwelle (leiser Ausklang zählt als Stimme) und 0,40 s Ausklang.
            thr_e = ruhe + 0.22 * (laut - ruhe)
            t = b
            while t > ws[-1]["e"] - 0.15 and lv(t - 0.05) < thr_e:
                t -= 0.05
            if b - (t + 0.40) > 0.1:
                gek += b - (t + 0.40)
                b = t + 0.40
        # Malo's-Kontroll-Lauf 09.10.: Das Ende eines Abschnitts wurde „bis zum Stimm-Ende“ um 0,17 s verlängert – über die
        # Clip-Grenze hinweg. Zu sehen war kurz der NÄCHSTE Clip (er steht an der Theke, noch nicht bereit), dann der Schnitt.
        # Ein Abschnitt darf nie über eine Clip-Grenze hinaus wachsen.
        # (Auch der Fall, dass ein Mini-Rest hinter der Grenze beim Glätten wieder angehängt wurde: liegt hinter einer Clip-
        # Grenze im Abschnitt kein behaltenes Wort mehr, endet der Abschnitt an der Grenze.)
        for jn in sorted(_joins):
            # (nie bei stummen Zeige-Strecken – dort gibt es keine Wörter, die Bildfolge IST der Inhalt; Kontroll-Lauf 09.10.:
            # die ganze Zubereitung fiel weg, weil vor ihr eine Clip-Grenze im selben Abschnitt lag)
            if a + 0.15 < jn < b and ws and not any((w["s"] + w["e"]) / 2 > jn for w in ws) \
                    and not any(z0 < b and z1 > jn for z0, z1 in _zeigen):
                gek += b - (jn - 0.02)
                b = jn - 0.02
                break
        # … und umgekehrt: liegt VOR einer Clip-Grenze im Abschnitt kein behaltenes Wort, beginnt er erst an der Grenze
        for jn in sorted(_joins, reverse=True):
            if a < jn < b - 0.15 and ws and not any((w["s"] + w["e"]) / 2 < jn for w in ws) \
                    and not any(z0 < jn and z1 > a for z0, z1 in _zeigen):
                gek += (jn + 0.02) - a
                a = jn + 0.02
                break
        if not ws and not _handlung_bleibt and b - a < 0.8 and not any(z0 < b and z1 > a for z0, z1 in _zeigen) \
                and all(lv(a + i * 0.05) < thr for i in range(int((b - a) / 0.05) + 1)):
            gek += b - a      # Reststück ohne Wort und ohne Ton (0,3 s Stillstand zwischen zwei Sätzen) – weg damit
            continue
        if b - a > 0.08:
            out.append([a, b])
    # Inhaber 08.10. (Premium, „mit Tofu“ klingt abgehackt/doppelt): Aus einer 0,4-s-Sprechpause zwischen „mit“ und „Tofu“
    # wurden 0,2 s herausgeschnitten – gespart ist nichts, aber der Ausklang des Wortes davor wird hörbar gekappt.
    # Ein Schnitt, der weniger als 0,25 s spart und kein gestrichenes Wort entfernt, wird zurückgenommen (kein Clip-Wechsel).
    ks_ = {(round(w["s"], 2), round(w["e"], 2)) for w in kept}
    weg = [w for w in WORDS if (round(w["s"], 2), round(w["e"], 2)) not in ks_]
    glatt = []
    for a, b in out:
        if glatt and 0.02 <= a - glatt[-1][1] < 0.25 and round(a, 2) not in _geteilt and not any(abs(a - j) < 0.2 for j in _joins) \
                and not any(w["s"] < a and w["e"] > glatt[-1][1] for w in weg):
            glatt[-1][1] = b
        else:
            glatt.append([a, b])
    out = glatt
    STATS["sprachkanten"] = {"ende_verlaengert_s": round(verl, 2), "anfang_gekuerzt_s": round(gek, 2), "leerlauf_raus_s": round(leer, 2)}
    return out


def zeigen_sichern(ranges):
    """Selbstkontrolle (09.10.: eine eigene Regel schnitt die ganze stumme Zubereitung weg – das Video hatte 13,8 statt 28 s):
    Jede vom Schnittplan vorgesehene stumme Bild-Strecke muss im Schnitt enthalten sein. Fehlt sie ganz oder teilweise, wird
    sie wieder eingesetzt und der Vorfall protokolliert."""
    zeig = [(float(z[0]), float(z[1])) for z in ((PLAN or {}).get("zeigen") or []) if isinstance(z, (list, tuple)) and len(z) >= 2]
    if not zeig:
        return ranges
    out, fehl = [list(r) for r in ranges], 0.0
    for z0, z1 in zeig:
        da = sum(max(0.0, min(b, z1) - max(a, z0)) for a, b in out)
        if da < 0.9 * (z1 - z0) - 0.1:
            fehl += (z1 - z0) - da
            out.append([z0 + 0.02, z1 - 0.02])
    if fehl <= 0:
        return ranges
    out.sort()
    ver = []
    for a, b in out:
        if ver and a <= ver[-1][1] + 0.02:
            ver[-1][1] = max(ver[-1][1], b)
        else:
            ver.append([a, b])
    STATS["zeigen_wiederhergestellt_s"] = round(fehl, 2)
    log(f"SELBSTKONTROLLE: {fehl:.1f} s der geplanten stummen Bild-Strecke fehlten im Schnitt – wieder eingesetzt")
    return ver


def schnittstellen_pruefung(ranges, kept):
    """Inhaber 09.10.: „Solche kleinen, offensichtlichen Fehler dürfen nicht durchrutschen – auch bei ungewohntem Material.
    Die KI soll alles selbst prüfen wie ein Cutter mit tausenden Stunden Erfahrung, 24/7, ohne dass ich draufschauen muss.“
    Der Ton entscheidet, WO geschnitten wird – aber ob ein Abschnitt gut AUSSIEHT, sieht man nur im Bild. Vor dem Rendern
    schaut die KI deshalb jede Schnittstelle an: drei Bilder vom Anfang und drei vom Ende jedes gesprochenen Abschnitts.
    Sie meldet, was ein Cutter sofort sähe – die Person ist am Anfang noch nicht bereit (richtet sich ein, schaut weg, wartet
    auf ein Zeichen), am Ende schon „fertig“ (dreht sich weg, greift nach etwas, schaut zum Kameramann), oder eine fremde
    Hand/Person ist am Rand im Bild. Die Korrektur ist fest begrenzt: Der Rand wird um höchstens 0,6 s gekürzt und nie in ein
    behaltenes Wort hinein (Anlauf 0,10 s, Ausklang 0,12 s bleiben immer)."""
    if not GEMINI_KEY or len(ranges) < 1:
        return ranges
    import base64
    zeig = [(float(z[0]), float(z[1])) for z in ((PLAN or {}).get("zeigen") or []) if isinstance(z, (list, tuple)) and len(z) >= 2]
    idx, parts = [], []
    tmp = os.path.join(A.out, "sp.jpg")
    try:
        for k, (a, b) in enumerate(ranges):
            ws = [w for w in kept if a - 0.05 <= (w["s"] + w["e"]) / 2 < b]
            if not ws or b - a < 0.9 or any(z0 < b and z1 > a for z0, z1 in zeig):
                continue
            idx.append(k)
            parts.append({"text": f"Abschnitt {k} ({b - a:.1f} s, Text: „{' '.join(w['t'] for w in ws)[:70]}“). ANFANG (0,0 / 0,2 / 0,5 s), dann ENDE (−0,5 / −0,2 / 0,0 s):"})
            for tq in (a + 0.02, a + 0.2, a + 0.5, b - 0.5, b - 0.2, b - 0.03):
                run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, tq):.3f}", "-i", A.src, "-frames:v", "1", "-vf", "scale=260:-2", "-q:v", "6", tmp])
                parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(open(tmp, "rb").read()).decode()}})
            if len(idx) >= 14:
                break
        if os.path.exists(tmp):
            os.remove(tmp)
        if not idx:
            return ranges
        res = gemini(
            "Du bist ein erfahrener Cutter und prüfst die Schnittstellen eines Kurzvideos, bevor es zum Kunden geht. Zu jedem "
            "Abschnitt siehst du drei Bilder vom ANFANG und drei vom ENDE. Beurteile nur, was man im Bild sieht:\n"
            "ANFANG – ist die Person schon bereit und beim Sprechen/Handeln? Fehler: Sie richtet sich noch ein, schaut noch weg "
            "oder nach unten (aufs Handy/Skript), wartet sichtbar, atmet tief ein, bewegt sich erst in Position.\n"
            "ENDE – hört der Abschnitt auf, solange die Person noch „im Satz“ ist? Fehler: Sie ist sichtbar fertig und dreht "
            "sich weg, schaut zum Kameramann, greift nach etwas, lässt die Haltung fallen, grinst/lacht nach dem Satz.\n"
            "STÖRUNG – eine zweite Person oder fremde Hand kommt ins Bild, etwas wird hereingereicht, jemand läuft durch.\n"
            "Gib je Abschnitt an: anfang_kuerzen und ende_kuerzen in Sekunden (0, wenn alles gut ist; sonst so viel, wie vom "
            "Rand weg muss, höchstens 0,6), stoerung (anfang / ende / keine) und in wenigen Wörtern den Grund. Im Zweifel 0 – "
            "kürze nur, wenn der Fehler in den Bildern klar zu sehen ist.",
            "Prüfe jetzt alle Abschnitte.", {"type": "object", "properties": {"abschnitte": {"type": "array", "items": {"type": "object", "properties": {
                "nr": {"type": "integer"}, "anfang_kuerzen": {"type": "number"}, "ende_kuerzen": {"type": "number"},
                "stoerung": {"type": "string"}, "grund": {"type": "string"}}, "required": ["nr", "anfang_kuerzen", "ende_kuerzen"]}}},
                 "required": ["abschnitte"]}, parts=parts, timeout=120)
    except Exception as e:  # noqa: BLE001
        log(f"Schnittstellen-Prüfung nicht möglich ({e})")
        return ranges
    out, n_k, s_k = [list(r) for r in ranges], 0, 0.0
    for d in (res or {}).get("abschnitte") or []:
        k = d.get("nr")
        if not isinstance(k, int) or k not in idx:
            continue
        a, b = out[k]
        ws = [w for w in kept if a - 0.05 <= (w["s"] + w["e"]) / 2 < b]
        ka = min(0.6, max(0.0, float(d.get("anfang_kuerzen") or 0)), max(0.0, ws[0]["s"] - 0.10 - a))
        # Am ENDE DES VIDEOS bleibt mehr Ausklang (0,35 s): dort blendet der Ton aus – mit 0,12 s klang „Capri-Sonne“ für die
        # Abnahme „mitten im Wort abgeschnitten“ (Lauf 10.10.).
        aus = 0.35 if k == len(out) - 1 else 0.12
        kb = min(0.6, max(0.0, float(d.get("ende_kuerzen") or 0)), max(0.0, b - (ws[-1]["e"] + aus)))
        if ka >= 0.08 or kb >= 0.08:
            ka, kb = (ka if ka >= 0.08 else 0.0), (kb if kb >= 0.08 else 0.0)
            out[k] = [a + ka, b - kb]
            n_k += 1
            s_k += ka + kb
            log(f"  Schnittstelle {k}: Anfang −{ka:.2f} s, Ende −{kb:.2f} s – {str(d.get('grund') or '')[:70]}")
    STATS["schnittstellen_pruefung"] = {"geprueft": len(idx), "gekuerzt": n_k, "sekunden": round(s_k, 2)}
    log(f"Schnittstellen-Prüfung (Bild): {len(idx)} Abschnitte angesehen, {n_k} am Rand gekürzt ({s_k:.2f} s)")
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


def wg_einsetzen():
    """Wortgenaue Zeiten gelten ab dem Schnitt (einmalig für alle Wörter). Davor – bei der Satzauswahl – gelten die alten."""
    if STATS.get("wg_eingesetzt"):
        return
    n_ = 0
    for w in WORDS:
        if "sa" in w and "s0" not in w:
            w["s0"], w["e0"] = w["s"], w["e"]
            w["s"], w["e"] = w["sa"], w["ea"]
            n_ += 1
    for a_, b_ in zip(WORDS, WORDS[1:]):   # Reihenfolge und Abstände bleiben gültig
        if b_["s"] < a_["e"]:
            b_["s"] = a_["e"]
        if b_["e"] < b_["s"] + 0.02:
            b_["e"] = b_["s"] + 0.02
    STATS["wg_eingesetzt"] = n_


def wortluecken(ranges, kept):
    """Wortgenau (09.10.2026): Liegt zwischen zwei behaltenen, sauber verorteten Wörtern eine Lücke ≥ 0,45 s, steckt darin
    etwas, das die Erkennung nicht als Wort gehört hat – meist ein „ähm“, ein Atemzug oder ein Schmatzen. Leise Lücken schneidet
    der normale Weg schon; laute blieben bisher stehen (Testlauf Novus-Prozess: vier „ähm“ im fertigen Clip). Die Lücke wird
    bis auf den Ausklang des Wortes davor (0,12 s) und den Anlauf des Wortes danach (0,07 s) herausgenommen. Nur bei
    Einzelvideos – in Ordner-Videos füllt dort oft eine Handlung das Bild."""
    if os.environ.get("VELOX_WORTGENAU", "0") != "1":
        return ranges
    if WG_EXTRA:   # zusätzlich gehörte „ähm“: als eigenes Stück herausnehmen (nie in ein behaltenes Wort hinein)
        _o = []
        for a, b in ranges:
            cur = a
            for fs, fe in sorted(WG_EXTRA):
                lo = max([w["e"] for w in kept if w["e"] <= fs + 0.05] + [a]); hi = min([w["s"] for w in kept if w["s"] >= fe - 0.05] + [b])
                x, y = max(fs - 0.02, lo + 0.03, cur), min(fe + 0.02, hi - 0.03, b)
                if a <= fs and fe <= b and y - x >= 0.12 and x - cur >= 0.1 and b - y >= 0.1:
                    _o.append([cur, x]); cur = y
            _o.append([cur, b])
        ranges = _o
    if PLAN:
        return ranges
    ks = sorted((w for w in kept if "s0" in w), key=lambda w: w["s"])
    out, n_ = [], 0
    for a, b in ranges:
        cur = a
        ws = [w for w in ks if a - 0.01 <= w["s"] and w["e"] <= b + 0.01]
        for w1, w2 in zip(ws, ws[1:]):
            if w2["i"] == w1["i"] + 1 and w2["s"] - w1["e"] >= 0.45:
                x, y = w1["e"] + 0.12, w2["s"] - 0.07
                if y - x >= 0.2 and x - cur >= 0.15:
                    out.append([cur, x])
                    cur = y
                    n_ += 1
        out.append([cur, b])
    if n_:
        STATS["wortluecken"] = n_
        log(f"Wortgenau: {n_} Lücken zwischen Wörtern herausgenommen (nicht erkannte Laute)")
    return out


_HUELLE = []


def _huelle():
    """Lautstärke der Tonspur in 10-ms-Schritten (dB, leicht geglättet) + Pegel lauter Sprache (90. Perzentil)."""
    if not _HUELLE:
        import wave
        with wave.open(wav) as f_:
            x = np.frombuffer(f_.readframes(f_.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        n_ = len(x) // 160
        db = 20 * np.log10(np.sqrt((x[:n_ * 160].reshape(n_, 160) ** 2).mean(1) + 1e-10))
        _HUELLE.extend([np.convolve(db, np.ones(3) / 3, mode="same"), float(np.percentile(db, 90))])
    return _HUELLE


def handschnitt(kept):
    """Schnittregel aus den Handschnitten für den Inhaber (Novus-Serie, 09.–10.10.; Messlatte 11.10.: Handschnitt 0–2,5 Fehler je
    100 Wörter, bisheriger Software-Schnitt 2–10). Arbeitet NUR mit den wortgenauen Zeiten (wortgenau.py) und dem Pegel:
      – Ein Wort endet dort, wo die Stimme ausklingt (Pegel fällt 22 dB unter laute Sprache), höchstens 0,15 s nach dem
        gemessenen Wortende und nie im nächsten Wort.
      – Zwei behaltene Wörter direkt hintereinander: Pause > 0,24 s → bis auf 0,10 s Ausklang und 0,06 s Anlauf heraus.
      – Dazwischen wird etwas gestrichen: Schnitt 0,08 s nach dem Ausklang (nie im gestrichenen Wort) bis 0,025 s vor dem
        nächsten behaltenen Wort (nie im gestrichenen Wort davor).
      – Anfang: 0,08 s vor dem ersten Wort; Ende: 0,3 s nach dem Ausklang des letzten.
    Rückgabe: Schnittliste oder None (dann gilt der bisherige Weg)."""
    kid = {w["i"] for w in kept}
    if len(kept) < 4 or sum(1 for w in kept if "s0" in w) < 0.9 * len(kept):
        return None
    env, L = _huelle()
    n_ = len(env)
    i0, i1 = min(kid), max(kid)
    # Füll-Laute fliegen immer raus – auch die, die der bisherige Weg nur im Untertitel versteckt hat (blieben hörbar)
    _fl = lambda w: re.sub(r"[^\wäöüß]", "", w["t"].lower()) in FILLER
    A_ = [{"s": w["s"], "e": w["e"], "k": w["i"] in kid and not _fl(w)} for w in WORDS[max(0, i0 - 1):i1 + 2]]
    A_ += [{"s": a_, "e": b_, "k": False} for a_, b_ in WG_EXTRA if A_[0]["s"] <= a_ <= A_[-1]["e"]]
    A_.sort(key=lambda x: x["s"])
    for x, y in zip(A_, A_[1:]):
        if y["s"] < x["e"]:
            if x["k"] and not y["k"]:
                y["s"] = x["e"]
            else:
                x["e"] = y["s"]
    ki_ = [j for j, x in enumerate(A_) if x["k"]]

    def tail(j):
        e = A_[j]["e"]
        lim = min(e + 0.15, A_[j + 1]["s"] if j + 1 < len(A_) else n_ / 100)
        k = int(e * 100)
        while k / 100 < lim and k < n_ and env[k] > L - 22:
            k += 1
        return max(e, min(k / 100, lim))

    cuts = []
    f0 = ki_[0]
    start = max(0.0, A_[f0]["s"] - 0.08, (A_[f0 - 1]["e"] + 0.01) if f0 > 0 else 0.0)
    for x, y in zip(ki_, ki_[1:]):
        ea, sb = tail(x), A_[y]["s"]
        if y == x + 1:
            if sb - ea > 0.24:
                cuts.append((ea + 0.10, sb - 0.06))
        else:
            ca = ea + min(0.08, max(0.0, A_[x + 1]["s"] - ea))
            cb = sb - min(0.025, max(0.0, sb - A_[y - 1]["e"]))
            if cb - ca >= 0.05:
                cuts.append((ca, cb))
    fl = ki_[-1]
    ende = min(tail(fl) + 0.3, (A_[fl + 1]["s"] - 0.02) if fl + 1 < len(A_) else n_ / 100, DUR)
    out, cur = [], start
    for a_, b_ in sorted(cuts):
        if a_ - cur >= 0.05:   # auch kurze Wörter („es“, „da“) bleiben – nur leere Splitter fallen weg
            out.append([cur, a_])
        cur = max(cur, b_)
    if ende - cur >= 0.1:
        out.append([cur, ende])
    # bildgenau: Anfang ab-, Ende aufrunden (nie ein Wort anschneiden), Überlappungen zusammenziehen
    res = []
    for a_, b_ in out:
        a_, b_ = np.floor(a_ * OF + 1e-6) / OF, np.ceil(b_ * OF - 1e-6) / OF
        if res and a_ <= res[-1][1]:
            res[-1][1] = max(res[-1][1], b_)
        else:
            res.append([float(a_), float(b_)])
    STATS["handschnitt"] = {"abschnitte": len(res), "schnitte": len(cuts)}
    return res if res else None


_LESE = {}


def lesekontrolle(words, kept):
    """Lesekontrolle vor dem Schnitt (12.10.): Nach dem Streichen bleiben manchmal Bruchstücke stehen – z. B. von einer
    Selbstkorrektur nur die erste Hälfte („einen Call mit dem Kunden oder mit dem [potenziellen Kunden besser gesagt] und
    erklären ihm“ → „… mit dem Kunden oder mit dem und erklären ihm“). Die KI liest den Text so, wie er im Video zu hören sein
    wird, und nennt NUR Wörter, die als Bruchstück in der Luft hängen. Gestrichen wird höchstens ein kleiner Teil (6 %)."""
    kid = [w["i"] for w in kept]
    key = (kid[0], kid[-1], len(kid), sum(kid) % 100003)
    if key in _LESE:
        return _LESE[key]
    ks = set(kid)
    text = " ".join((f"[{w['i']}]{w['t']}" if w["i"] in ks else f"(({w['i']}:{w['t']}))") for w in words if kept[0]["i"] <= w["i"] <= kept[-1]["i"])
    res = gemini(
        "Du bist Schlussredakteur. Unten steht ein gesprochener Text, aus dem schon Teile herausgeschnitten wurden: Wörter in "
        "((Nummer:doppelten Klammern)) sind WEG und nicht mehr zu hören; die übrigen Wörter tragen eine [Nummer] und sind zu hören. Lies "
        "NUR die nummerierten Wörter hintereinander, so wie der Zuschauer sie hört. Suche Bruchstücke, die durch das "
        "Herausschneiden in der Luft hängen: ein angefangener Satzteil, dessen Fortsetzung weg ist („oder mit dem“ vor einem "
        "neuen Satzteil), ein einzelnes übrig gebliebenes Wort eines gestrichenen Neuanlaufs, ein doppeltes Wort direkt "
        "hintereinander („auch den auch den“), ein „also“/„und“ ganz am Ende ohne Fortsetzung. Gib die Nummern genau dieser "
        "Wörter zurück – so wenige wie möglich. Ganze, verständliche Sätze bleiben unangetastet, auch wenn sie umgangssprachlich "
        "sind; inhaltlich wird nichts gekürzt. Ist alles flüssig lesbar: leere Liste. "
        "Zusätzlich „rein“: Fehlt einem Satz durch das Herausschneiden ein einzelnes nötiges Wort – das Subjekt („dass ((wir)) "
        "gerade 25 Nachrichten abschicken“), ein Artikel, ein Hilfsverb –, dann nenne die Nummer genau dieses weggeschnittenen "
        "Worts, damit es wieder hineinkommt. Höchstens vier Wörter, nie Füll-Laute (äh, ähm), nie ganze gestrichene Sätze.",
        text,
        {"type": "object", "properties": {"raus": {"type": "array", "items": {"type": "integer"}},
                                          "rein": {"type": "array", "items": {"type": "integer"}}, "warum": {"type": "string"}},
         "required": ["raus"]}, timeout=60, deep=True)
    raus = {i for i in (res or {}).get("raus") or [] if isinstance(i, int) and i in ks}
    _alle = {w["i"]: w for w in words}
    rein = {i for i in (res or {}).get("rein") or [] if isinstance(i, int) and i in _alle and i not in ks
            and kept[0]["i"] < i < kept[-1]["i"] and re.sub(r"[^\wäöüß]", "", _alle[i]["t"].lower()) not in FILLER}
    if len(rein) > 4:
        rein = set()
    if rein:
        log("Lesekontrolle: fehlendes Wort wieder hinein (" + " ".join(_alle[i]["t"] for i in sorted(rein)) + ")")
    STATS["lesekontrolle_rein"] = sorted(rein)
    if len(raus) > max(2, 0.06 * len(kid)):
        log(f"Lesekontrolle: Vorschlag zu groß ({len(raus)} Wörter) – verworfen")
        raus = set()
    elif raus:
        log(f"Lesekontrolle: {len(raus)} Bruchstück-Wörter raus (" + " ".join(w["t"] for w in words if w["i"] in raus)[:80]
            + f") – {str((res or {}).get('warum') or '')[:100]}")
    STATS["lesekontrolle"] = sorted(raus)
    _LESE[key] = (raus, rein)
    return raus, rein


def make_cut(n, m, step):
    """Schnitt eines Moments bis zum fertigen Schnitt-Video (ohne Untertitel): Schnittliste, Rendern, Hörprobe
    (sicherer Neuschnitt bei angeschnittenen Wörtern), Fetzen am Anfang/Ende wegschneiden."""
    wg_einsetzen()
    words = WORDS[m["start"]:m["end"] + 1]
    ranges, kept = keep_ranges(words, safe=KONSERVATIV[0], extra_drop=m.get("drop") or set())
    if not ranges:
        return None
    _xd, _xk = set(m.get("drop") or set()), set()
    if os.environ.get("VELOX_LESEKONTROLLE", "1") == "1" and not PLAN and len(kept) >= 12:
        try:
            _raus, _rein = lesekontrolle(words, kept)
            if _raus or _rein:
                _r2, _k2 = keep_ranges(words, safe=KONSERVATIV[0], extra_drop=(m.get("drop") or set()) | _raus, extra_keep=_rein)
                if _r2 and len(_k2) >= 0.9 * len(kept):
                    ranges, kept = _r2, _k2
                    _xd, _xk = _xd | _raus, set(_rein)
        except Exception as _el:  # noqa: BLE001
            log(f"Lesekontrolle ausgefallen ({_el})")
    choose_speed(kept)
    ranges = split_joins(ranges, kept)
    ranges, kept = limit60(ranges, kept)
    hand = None
    if HAND_AN:   # auch die Sicherheits-Fassung schneidet wortgenau – „vorsichtig“ heißt dort: weniger Wörter streichen, nicht ungenauer schneiden
        try:
            hand = handschnitt(kept)
        except Exception as _eh:  # noqa: BLE001
            log(f"Handschnitt-Regel nicht möglich ({_eh}) – bisheriger Weg")
    if hand:
        log(f"Handschnitt-Regel: {len(hand)} Abschnitte aus wortgenauen Zeiten")
        ranges = hand
    else:
        ranges = tidy(wortluecken(tidy(sprachkanten(tidy(ranges), kept)), kept))
        ranges = tidy(zeigen_sichern(tidy(ranges if KONSERVATIV[0] else schnittstellen_pruefung(ranges, kept))))
    if os.environ.get("VELOX_DEBUG"):  # Fehlersuche: Wörter (mit „raus“) und Schnittliste ablegen
        kid = {w["i"] for w in kept}
        json.dump({"words": [[w["t"], round(w["s"], 2), round(w["e"], 2), w["i"] in kid] for w in words],
                   "ranges": [[round(a, 2), round(b, 2)] for a, b in ranges], "sil": SIL},
                  open(os.path.join(A.out, f"debug-{n}.json"), "w"), ensure_ascii=False)
    ranges, kept = kurz_anfang([list(r_) for r_ in ranges], kept)
    try:
        ranges, kept = schluss_pruefung(ranges, kept)
    except Exception as _es:  # noqa: BLE001
        log(f"Schluss-Prüfung übersprungen ({_es})")
    for _ in range(3):   # „Tschüss. Hallo. Hallo.“ – so oft, bis keine Begrüßung mehr am Ende steht
        _vor = len(kept)
        ranges, kept = gruss_am_ende(ranges, kept)
        if len(kept) == _vor:
            break
    ranges = tidy(take_anlauf([list(r_) for r_ in ranges], kept))
    step(0.2, "Setzt die Schnitte und glättet die Übergänge …")
    cut, secs = render_cut(n, ranges)
    step(0.55, "Prüft den Schnitt (Hörprobe) …")
    heard = hear(cut)
    words_out, miss = align(ranges, kept, heard)
    STATS["missing_first"] = miss[:20]
    # Handschnitt-Regel: Die Hörprobe verhört sich bei 3–5 % der Wörter ohnehin (SMMA, ein/dein) – erst bei deutlich mehr
    # (über 8 %) gilt der Schnitt als beschädigt. Sonst würde der genaue Schnitt fast immer durch den groben ersetzt.
    # Testlauf 13.10. (Cheesecake-Umfragen, mehrere Sprecher): „Hallo“, „Ja.“, „Natürlich.“ der Passanten wurden in der Hörprobe
    # nicht erkannt → sicherer Modus → gröberer Schnitt, in dem gestrichene Wörter („Okay“, „Äh“) wieder hörbar waren. Ein Schnitt
    # kann ein Wort nur beschädigen, wenn es an einem Schnitt liegt. Für die Entscheidung zählen deshalb nur nicht gehörte Wörter
    # am Rand eines Abschnitts (≤ 0,3 s); mitten im Abschnitt ist es ein Verhörer, kein Schnittschaden.
    _mi = set(MISS_I)
    _rand = [w for w in kept if w["i"] in _mi and any(abs(w["s"] - a_) < 0.3 or abs(w["e"] - b_) < 0.3 for a_, b_ in ranges)]
    # Gegenprobe am ROHMATERIAL (Runde 4, 13.10.: in Umfragen mit vielen kurzen Abschnitten liegt fast jedes Wort „am Schnitt“,
    # der grobe Neuschnitt kam weiter): Die Hörprobe hört dieselbe Stelle im UNGESCHNITTENEN Ton. Versteht sie das Wort auch dort
    # nicht („Lotuscreme“ → „Lothrauch“, „Laba“), ist es ein Verhörer – der Schnitt hat nichts beschädigt. Nur Wörter, die im
    # Rohmaterial zu hören sind und im Schnitt nicht, zählen.
    if len(_rand) > (max(4, len(kept) * 0.08) if hand else max(2, len(kept) * 0.03)):
        _echt, _snip = [], os.path.join(A.out, "gegenprobe.wav")
        try:
            for w in _rand[:12]:
                run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, w['s'] - 0.7):.3f}", "-t", f"{w['e'] - w['s'] + 1.4:.3f}", "-i", wav_asr, _snip])
                _tr = mlx_whisper.transcribe(_snip, **ASR, temperature=0.0,
                                             initial_prompt=((BRAND + ". ") if BRAND else "") + ("Ähm, äh." if LANG == "de" else ""))
                _hw = [norm(p_) for sg_ in _tr["segments"] for x_ in sg_.get("words", []) for p_ in x_["word"].split("-") if norm(p_)]
                _ww = [norm(p_) for p_ in w["t"].split("-") if norm(p_)]
                if all(any(difflib.SequenceMatcher(a=a_, b=b_).ratio() >= 0.75 for b_ in _hw) for a_ in _ww):
                    _echt.append(w)
            if os.path.exists(_snip):
                os.remove(_snip)
            log(f"Gegenprobe am Rohmaterial: {len(_rand[:12]) - len(_echt)} von {len(_rand[:12])} Wörtern sind Verhörer, {len(_echt)} wirklich nur im Schnitt weg"
                + (f" ({', '.join(w['t'] for w in _echt[:6])})" if _echt else ""))
            _rand = _echt + _rand[12:]
        except Exception as _eg:  # noqa: BLE001
            log(f"Gegenprobe übersprungen ({_eg})")
    STATS["missing_am_schnitt"] = [w["t"] for w in _rand][:20]
    if len(_rand) > (max(4, len(kept) * 0.08) if hand else max(2, len(kept) * 0.03)):  # Wörter angeschnitten → vorsichtiger neu schneiden
        log(f"Hörprobe: {len(miss)} Wörter unklar ({', '.join(miss[:6])}) – schneide im sicheren Modus neu")
        step(0.6, "Schneidet eine Stelle vorsichtiger nach …")
        ranges, kept = keep_ranges(words, safe=True, extra_drop=_xd, extra_keep=_xk)
        # Fehler bis 06.10.: im sicheren Modus fehlte die Trennung an den Clip-Grenzen (und damit Regel 109) – bei Malo's
        # lief zweimal genau dieser Pfad, die Abschnitte gingen dann wieder über Clip-Wechsel hinweg.
        ranges, kept = limit60(split_joins(ranges, kept), kept)
        ranges = tidy(wortluecken(tidy(sprachkanten(tidy(ranges), kept)), kept))
        ranges = tidy(zeigen_sichern(tidy(ranges if KONSERVATIV[0] else schnittstellen_pruefung(ranges, kept))))
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
    if KONSERVATIV[0]:
        ca = cb = 0.0   # die Sicherheits-Fassung schneidet an Anfang und Ende nichts nach
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


KONSERVATIV = [False]   # Sicherheits-Fassung: vorsichtig schneiden, ohne die optionalen Kürzungen (siehe Schluss-Abnahme)


def zweiter_schnitt(n, c):
    """Inhaber 09.10.: „Bau einen zweiten Schnitt ein, der korrigiert, was im ersten Lauf nicht korrigiert wurde.“
    Ein Cutter hört sich den fertigen Schnitt noch einmal an und zieht nach. Genau das passiert hier – gemessen am FERTIGEN
    Ton (ohne Musik), nicht an den Zeiten der Worterkennung:
      • Leerlauf: Bleibt zwischen zwei Wörtern 0,5–1,5 s Ruhe/Atmen stehen (außerhalb stummer Bild-Strecken), wird sie auf
        0,2 s gekürzt. Längere Ruhe ist gewollte Handlung und bleibt.
      • Splitter: Ein Abschnitt unter 0,3 s ohne behaltenes Wort fällt weg.
    Nur wenn etwas gefunden wird, wird neu gerendert. Rückgabe: neuer Schnitt oder None."""
    try:
        ranges, kept = [list(r) for r in c["ranges"]], c["kept"]
        fmt = str((PLAN or {}).get("format") or (STATS.get("story_format") or {}).get("format") or "").lower()
        zeig = [(float(z[0]), float(z[1])) for z in ((PLAN or {}).get("zeigen") or []) if isinstance(z, (list, tuple)) and len(z) >= 2]
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", c["cut"], "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                             capture_output=True).stdout
        x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
        if len(x) < 16000:
            return None
        lv = [20 * float(np.log10(np.sqrt((x[i:i + 320] ** 2).mean()) + 1e-7)) for i in range(0, len(x) - 320, 320)]
        hoer = sorted(v for v in lv if v > -80.0)
        if len(hoer) < 50:
            return None
        laut, ruhe = hoer[int(len(hoer) * 0.9)], hoer[int(len(hoer) * 0.15)]
        thr = ruhe + 0.4 * (laut - ruhe)
        offs, t = [], 0.0
        for a, b in ranges:
            offs.append((a, b, t))
            t += olen(a, b)
        quelle = lambda to: next(((a + (to - o) * SPEED[0], k) for k, (a, b, o) in enumerate(offs) if o <= to < o + olen(a, b)), (None, None))
        weg, i, n_l = [], 0, 0
        while i < len(lv):
            if lv[i] >= thr:
                i += 1
                continue
            j = i
            while j < len(lv) and lv[j] < thr:
                j += 1
            g0, g1 = i * 0.02, j * 0.02
            i = j
            if not (0.5 <= g1 - g0 <= 1.5) or g0 < 0.3 or g1 > t - 0.5:
                continue
            (s0, k0), (s1, k1) = quelle(g0 + 0.11), quelle(g1 - 0.09)
            if s0 is None or s1 is None or k0 != k1 or s1 - s0 < 0.15:
                continue                       # Ruhe über einen Schnitt hinweg: dort hat der erste Schnitt schon gekürzt
            if any(z0 < s1 and z1 > s0 for z0, z1 in zeig) or (re.search(r"zubereit|anleitung|tutorial|rezept|behind", fmt) and not zeig):
                continue                       # stumme Bild-Strecke bzw. erzählte Zubereitung: Handlung bleibt
            if any(w["s"] < s1 and w["e"] > s0 and (w["s"] + w["e"]) / 2 > s0 and (w["s"] + w["e"]) / 2 < s1 for w in kept):
                continue                       # in der „Ruhe“ liegt die Mitte eines behaltenen Wortes → leises Wort, kein Leerlauf
            weg.append((s0, s1))
            n_l += 1
        # • Füll-Laute (Testlauf 13.10., Cheesecake-Umfragen: bis zu drei hörbare „ähm“ je Video, obwohl gestrichen – der
        #   erste Schnitt saß um Zehntelsekunden daneben): Hört man im FERTIGEN Ton noch ein „äh/ähm“, das nicht auf einem
        #   behaltenen Wort liegt, wird genau diese Stelle herausgenommen.
        n_f = 0
        try:
            # Zwei Hörproben: die normale und eine, die ausdrücklich auf Füll-Laute achtet (so hört auch die Messlatte). Ein
            # Fund zählt nur, wenn dort wirklich eine Stimme zu hören ist (Pegel über der Ruhe-Schwelle) – keine Atempause.
            _tmpf = os.path.join(A.out, "fuell.wav")
            run(["ffmpeg", "-v", "error", "-y", "-i", c["cut"], "-vn", "-ac", "1", "-ar", "16000", _tmpf])
            _r2 = mlx_whisper.transcribe(_tmpf, **ASR, temperature=0.0, initial_prompt="Äh... ähm... also... äh, ja, genau, ähm.")
            os.remove(_tmpf)
            _h2 = [{"t": x_["word"].strip(), "s": float(x_["start"]), "e": float(x_["end"]), "p": float(x_.get("probability", 1.0))}
                   for sg_ in _r2["segments"] for x_ in sg_.get("words", []) if x_["word"].strip()]
            _schon = []
            for h_ in hear(c["cut"]) + _h2:
                if norm(h_["t"]) not in FILLER or h_.get("p", 1.0) < 0.5 or h_["e"] - h_["s"] < 0.08 or h_["s"] < 0.15 or h_["e"] > t - 0.3:
                    continue
                if any(h_["s"] < e_ and h_["e"] > s_ for s_, e_ in _schon):
                    continue                   # von beiden Hörproben gefunden → nur einmal
                _pegel = lv[int(h_["s"] / 0.02):max(int(h_["s"] / 0.02) + 1, int(h_["e"] / 0.02))]
                if not _pegel or sorted(_pegel)[len(_pegel) // 2] < thr:
                    continue                   # dort ist es still → kein Laut, nichts zu schneiden
                _schon.append((h_["s"], h_["e"]))
                if any(min(h_["e"], w_[2]) - max(h_["s"], w_[1]) > 0.3 * (h_["e"] - h_["s"]) and norm(w_[0]) not in FILLER
                       for w_ in c["words_out"]):
                    continue                   # liegt auf einem Untertitel-Wort → könnte Teil des Wortes sein
                (s0, k0), (s1, k1) = quelle(h_["s"] + 0.01), quelle(h_["e"] - 0.01)
                if s0 is None or s1 is None or k0 != k1 or s1 - s0 < 0.06:
                    continue
                if any(s0 < (w["s"] + w["e"]) / 2 < s1 for w in kept):
                    continue
                weg.append((s0, s1))
                n_f += 1
        except Exception as _ef:  # noqa: BLE001
            log(f"Füll-Laut-Kontrolle übersprungen ({_ef})")
        neu = subtract(ranges, weg) if weg else ranges
        vor = len(neu)
        neu = [r for r in neu if r[1] - r[0] >= 0.3 or any(r[0] - 0.05 <= (w["s"] + w["e"]) / 2 < r[1] for w in kept)
               or any(z0 < r[1] and z1 > r[0] for z0, z1 in zeig)]
        n_s = vor - len(neu)
        if not n_l and not n_s and not n_f:
            log("Zweiter Schnitt: fertigen Ton nachgemessen – nichts zu korrigieren")
            STATS["zweiter_schnitt"] = {"leerlauf": 0, "splitter": 0, "fuell": 0}
            return None
        neu = tidy(zeigen_sichern(tidy(neu)))
        log(f"Zweiter Schnitt: {n_l} Leerstellen (0,5–1,5 s) gekürzt, {n_s} Splitter entfernt, {n_f} hörbare Füll-Laute herausgenommen – rendert neu")
        cut, secs = render_cut(n, neu)
        words_out, _ = align(neu, kept, hear(cut))
        STATS["zweiter_schnitt"] = {"leerlauf": n_l, "splitter": n_s, "fuell": n_f, "sekunden_vorher": c["secs"], "sekunden_nachher": secs}
        return {**c, "ranges": neu, "cut": cut, "secs": secs, "words_out": words_out}
    except Exception as e:  # noqa: BLE001
        log(f"Zweiter Schnitt nicht möglich ({e}) – die erste Fassung bleibt")
        return None


ABNAHME_NOTE = float(os.environ.get("VELOX_ABNAHME_NOTE", "8"))      # bestanden erst ab dieser Note – und nur ohne Schnittfehler
ABNAHME_RUNDEN = int(os.environ.get("VELOX_ABNAHME_RUNDEN", "3"))    # so oft wird gezielt ausgebessert und neu geprüft


def gezielt_reparieren(n, c, probleme, streichen=None):
    """Inhaber 10.10.: „Nicht komplett von vorne neu schneiden – nur die Sachen verbessern, die nicht passen.“
    Die Abnahme nennt Sekunde und Fehler. Hier wird nur die Schnittstelle angefasst, die dazu gehört; alles andere bleibt
    Bild für Bild, wie es war (keine neue Wortauswahl, keine neue KI-Entscheidung über den Rest):
      • „abgehackt / abgeschnitten / zu früh“ → an dieser Stelle mehr Luft (höchstens bis zum nächsten gestrichenen Wort und
        nie über einen Clip-Wechsel hinweg – sonst käme der Fehler zurück, wegen dem dort geschnitten wurde).
      • „Pause / Leerlauf / zu spät / hängt nach“ → an dieser Stelle bis knapp ans Wort kürzen (nie in stummen Bild-Strecken).
      • streichen = Wort-Nummern, die laut Nachschnitt raus sollen (Doppeltes, Panne): nur diese Wörter herausnehmen.
    Rückgabe: neuer Schnitt oder None (nichts gezielt zu tun)."""
    try:
        ranges, kept, words = [list(r) for r in c["ranges"]], list(c["kept"]), c["words"]
        kid = {w["i"] for w in kept}
        raus = [w for w in WORDS if w["i"] not in kid]
        joins = [float(j) for j in ((PLAN or {}).get("joins") or []) if isinstance(j, (int, float))]
        zeig = [(float(z[0]), float(z[1])) for z in ((PLAN or {}).get("zeigen") or []) if isinstance(z, (list, tuple)) and len(z) >= 2]
        im_zeig = lambda a, b: any(z0 < b and z1 > a for z0, z1 in zeig)
        offs, t = [], 0.0
        for a, b in ranges:
            offs.append(t)
            t += olen(a, b)
        total, getan = t, []

        def ende_bis(k, mehr):      # Abschnitt k hinten verlängern, begrenzt
            e = ranges[k][1]
            lim = e + mehr
            lim = min([lim] + [w["s"] - 0.02 for w in raus if w["s"] >= e - 0.03] + [j - 0.02 for j in joins if j > e - 0.01])
            if k + 1 < len(ranges):
                lim = min(lim, ranges[k + 1][0] - 0.02) if ranges[k + 1][0] > e else lim
            return max(e, lim)

        def anfang_bis(k, mehr):    # Abschnitt k vorne verlängern, begrenzt
            s0 = ranges[k][0]
            lim = max([s0 - mehr, 0.0] + [w["e"] + 0.02 for w in raus if w["e"] <= s0 + 0.03] + [j + 0.02 for j in joins if j < s0 + 0.01])
            if k > 0:
                lim = max(lim, ranges[k - 1][1] + 0.02) if ranges[k - 1][1] < s0 else lim
            return min(s0, lim)

        letztes = lambda k: max((w["e"] for w in kept if ranges[k][0] - 0.05 <= (w["s"] + w["e"]) / 2 < ranges[k][1] + 0.05), default=None)
        erstes = lambda k: min((w["s"] for w in kept if ranges[k][0] - 0.05 <= (w["s"] + w["e"]) / 2 < ranges[k][1] + 0.05), default=None)
        for p in probleme or []:
            txt, art = str(p.get("was", "")).lower(), p.get("art")
            try:
                sek = float(p.get("sek", 0))
            except (TypeError, ValueError):
                continue
            luft = re.search(r"abgehackt|abgeschnitten|angeschnitten|verschluckt|mitten im wort|zu früh|abrupt|bricht|hart|fehlt", txt)
            kurz = None if luft else re.search(r"pause|lücke|leerlauf|stille|hänger|hängt|zu spät|zu lang|atm|wartet|verzöger|nachlauf|totzeit", txt)
            if not luft and not kurz:
                continue
            if art == "anfang" or sek <= 0.6:
                if luft:
                    neu = anfang_bis(0, 0.15)
                else:
                    e1 = erstes(0)
                    neu = max(ranges[0][0], e1 - 0.10) if e1 is not None and not im_zeig(*ranges[0]) else ranges[0][0]
                if abs(neu - ranges[0][0]) >= 0.03:
                    getan.append(f"Anfang {'früher' if luft else 'später'} ({neu - ranges[0][0]:+.2f} s)")
                    ranges[0][0] = neu
                continue
            if art == "ende" or sek >= total - 0.8:
                if luft:
                    neu = ende_bis(len(ranges) - 1, 0.35)
                else:
                    l1 = letztes(len(ranges) - 1)
                    neu = min(ranges[-1][1], l1 + 0.40) if l1 is not None and not im_zeig(*ranges[-1]) else ranges[-1][1]
                if abs(neu - ranges[-1][1]) >= 0.03:
                    getan.append(f"Ende {'länger' if luft else 'kürzer'} ({neu - ranges[-1][1]:+.2f} s)")
                    ranges[-1][1] = neu
                continue
            k = min(range(1, len(ranges)), key=lambda k_: abs(offs[k_] - sek), default=None)
            if k is None or abs(offs[k] - sek) > 1.2:
                continue                      # der Befund liegt an keiner Schnittstelle → hier nichts anfassen
            if luft:
                e_n, s_n = ende_bis(k - 1, 0.12), anfang_bis(k, 0.10)
            else:
                l1, e1 = letztes(k - 1), erstes(k)
                e_n = min(ranges[k - 1][1], l1 + 0.10) if l1 is not None and not im_zeig(*ranges[k - 1]) else ranges[k - 1][1]
                s_n = max(ranges[k][0], e1 - 0.08) if e1 is not None and not im_zeig(*ranges[k]) else ranges[k][0]
            if abs(e_n - ranges[k - 1][1]) >= 0.03 or abs(s_n - ranges[k][0]) >= 0.03:
                getan.append(f"Schnitt bei {offs[k]:.1f} s {'mehr Luft' if luft else 'gekürzt'} ({e_n - ranges[k - 1][1]:+.2f}/{ranges[k][0] - s_n:+.2f} s)")
                ranges[k - 1][1], ranges[k][0] = e_n, s_n
        if streichen:
            weg, lauf = [], []
            for x, w in enumerate(kept + [None]):
                if w is not None and w["i"] in streichen:
                    lauf.append(x)
                    continue
                if lauf:
                    davor = kept[lauf[0] - 1] if lauf[0] > 0 else None
                    a_ = davor["e"] + 0.12 if davor else kept[lauf[0]]["s"] - 0.05
                    b_ = w["s"] - 0.08 if w is not None else kept[lauf[-1]]["e"] + 0.3
                    if b_ - a_ > 0.1 and not im_zeig(a_, b_):
                        weg.append((a_, b_))
                    lauf = []
            if weg:
                ranges = subtract(ranges, weg)
                kept = [w for w in kept if w["i"] not in streichen]
                getan.append(f"{len(weg)} Stelle(n) mit {len(streichen)} Wörtern herausgenommen")
        if not getan:
            return None
        neu = tidy(zeigen_sichern(tidy(ranges)))
        if neu == [list(r) for r in c["ranges"]]:
            return None
        log("Gezielte Reparatur: " + "; ".join(getan[:5]) + " – der Rest des Schnitts bleibt unverändert")
        cut, secs = render_cut(n, neu)
        words_out, _ = align(neu, kept, hear(cut))
        STATS.setdefault("gezielt_repariert", []).extend(getan[:8])
        return {**c, "ranges": neu, "kept": kept, "cut": cut, "secs": secs, "words_out": words_out}
    except Exception as e:  # noqa: BLE001
        log(f"Gezielte Reparatur nicht möglich ({e}) – die Fassung bleibt")
        return None


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
    wfin = c["words_out"]
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
                wfin = c["words_out"]
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
            wfin = words_out
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
                    wfin = words_out
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
                    wfin = wo3
                    sc2 = sense_check(f, c)
                    if sc2:
                        STATS["sinn"].update(ok_nach=sc2["ok"], note_nach=sc2.get("note"), probleme_nach=[p["was"][:100] for p in sc2["probleme"]])
                        log(f"Sinn-Check nach Nachschnitt: {'ok' if sc2['ok'] else 'noch Hinweise'} (Note {sc2.get('note')})")
    step(0.945, "Zweiter Schnitt: hört den fertigen Schnitt nach und zieht nach …")
    c4 = zweiter_schnitt(n, c)
    if c4:
        c = c4
        QUIZ.pop(n, None)
        wo4, wieder4 = reapply_fixes(c["words_out"], (revs_done if rev else []))
        f = burn(n, c["cut"], wo4, card_title, c["ranges"], c["kept"])
        wfin = wo4
    # ── Schluss-Abnahme mit Korrektur-Schleife (Inhaber 10.10.: „Das Endvideo muss wirklich fehlerfrei sein … ich bin nicht
    # immer erreichbar … nicht komplett von vorne neu schneiden, nur die Sachen verbessern, die nicht passen … kontrollieren,
    # ausbessern, wieder kontrollieren – erst wenn keine Fehler mehr drin sind, geht es an den Kunden“).
    # Ablauf: Das Video, das ausgeliefert würde, wird abgenommen. Bestanden ist es nur mit Note ≥ 8 UND ohne Schnittfehler UND
    # ohne offene Untertitel-Korrektur. Sonst wird GEZIELT repariert (gezielt_reparieren: nur die bemängelte Stelle), neu
    # eingebrannt und wieder abgenommen – bis zu ABNAHME_RUNDEN Mal. Von jeder Runde bleibt die beste Fassung liegen; macht eine
    # Reparatur es schlechter, gilt wieder die beste. Hilft gezieltes Reparieren nicht, kommt als letzter Versuch die vorsichtige
    # Sicherheits-Fassung. Es gibt keine Nachricht an den Inhaber und kein Warten auf eine Freigabe.
    pruefung = None
    if not A.no_review:
        f_best = f[:-4] + "_best.mp4"
        try:
            import shutil
            revs0 = list(revs_done) if rev else []
            stat_keys = ("sprachkanten", "schnittstellen_pruefung", "zweiter_schnitt", "safe_mode", "gezielt_repariert")
            best, verlauf, bestanden, nur_ut, test = None, [], False, False, os.environ.get("VELOX_TEST_ZWEIFEL")

            def abnehmen(c_, f_, w_, name):
                """Fertige Fassung benoten und – wenn sie die bisher beste ist – als beste ablegen. → (Befund, bestanden, offene Schnittfehler, Untertitel neu, Korrekturen)"""
                e_ = gemini_review(f_, w_, card_title)
                if not e_ or not isinstance(e_.get("note"), (int, float)):
                    return None, False, [], w_, []
                sch = [p_ for p_ in e_.get("probleme") or [] if p_.get("art") in CUT_KINDS
                       and not re.search(r"aussprach|ausgesprochen|dialekt|grammatik|akzent|betonung", str(p_.get("was", "")).lower())]
                if (PLAN or {}).get("zeigen"):  # stumme Bildfolge ist gewollt
                    sch = [p_ for p_ in sch if not re.search(r"lücke|pause|stille|ohne sprache|kein ton", str(p_.get("was", "")).lower())]
                w2_, fx_ = caption_round(e_, w_, c_["secs"])
                return e_, float(e_["note"]) >= ABNAHME_NOTE and not sch and not fx_, sch, w2_, fx_

            for runde in range(ABNAHME_RUNDEN + 1):
                step(0.947 + 0.0005 * runde, "Schluss-Abnahme: prüft das Video, das ausgeliefert wird …" if not runde
                     else f"Schluss-Abnahme: prüft die ausgebesserte Fassung (Runde {runde + 1}) …")
                end, ok, schwer, w_neu, fx = abnehmen(c, f, wfin, f"Runde {runde + 1}")
                if not end:
                    log("Schluss-Abnahme: keine Bewertung erhalten – es bleibt bei der bisher besten Fassung")
                    break
                note = float(end["note"])
                if test and not runde:  # nur zum Testen des Wegs: „sek:art:text“ erzwingt einen Befund in der ersten Runde
                    t_ = test.split(":", 2)
                    ok, schwer = False, schwer + ([{"sek": float(t_[0]), "art": t_[1], "was": t_[2]}] if len(t_) == 3 else [])
                key = note - 0.5 * len(schwer) - 0.25 * len(fx)
                verlauf.append({"note": note, "schnittfehler": len(schwer), "untertitel": len(fx)})
                log(f"Schluss-Abnahme Runde {runde + 1}: Note {note:.0f}/10, {len(schwer)} Schnittfehler, {len(fx)} Untertitel-Korrekturen"
                    + (" – bestanden" if ok else "") + ("; " + "; ".join(f"{p_.get('sek', 0):.0f}s {str(p_.get('was', ''))[:70]}" for p_ in schwer[:3]) if schwer else ""))
                if best is None or key > best["key"] or nur_ut:  # (nur Schreibweise im Untertitel berichtigt = derselbe Schnitt, gilt)
                    shutil.copyfile(f, f_best)
                    best = {"key": key, "c": c, "wfin": wfin, "note": note, "end": end, "ok": ok, "runde": runde + 1, "sf": len(schwer),
                            "stats": {k_: STATS.get(k_) for k_ in stat_keys}}
                elif runde:  # die Reparatur hat es nicht besser gemacht → zurück zur besten Fassung, gezielt geht hier nichts mehr
                    log(f"Schluss-Abnahme: Runde {runde + 1} ist nicht besser als Runde {best['runde']} – die bessere Fassung gilt weiter")
                    break
                if ok:
                    bestanden = True
                    break
                if runde == ABNAHME_RUNDEN:
                    break
                revs0.append(end)
                nur_ut = False
                c_r = gezielt_reparieren(n, c, schwer) if schwer else None
                if c_r is None and schwer:  # kein Rand-Problem → Satz-Ebene (Doppeltes, Panne, Versprecher)
                    m_r = gemini_fix_story(c["m"], c["kept"], schwer)
                    if m_r:
                        alt_d, neu_d = set(c["m"].get("drop") or set()), set(m_r.get("drop") or set())
                        if m_r["start"] == c["m"]["start"] and m_r["end"] == c["m"]["end"] and neu_d > alt_d:
                            c_r = gezielt_reparieren(n, c, [], streichen=neu_d - alt_d)  # nur diese Wörter herausnehmen
                            if c_r:
                                c_r["m"] = m_r
                        else:  # es kommt Text dazu → dieser Ausschnitt muss neu gesetzt werden (Erkennung und Montage bleiben)
                            c_r = make_cut(n, fix_bounds(m_r), step)
                            if c_r and c_r["secs"] < 0.85 * c["secs"]:
                                c_r = None
                if c_r:
                    c = c_r
                    QUIZ.pop(n, None)
                    wfin, _ = reapply_fixes(c["words_out"], revs0)
                    f = burn(n, c["cut"], wfin, card_title, c["ranges"], c["kept"])
                elif fx:
                    log(f"Schluss-Abnahme: {len(fx)} Untertitel korrigiert ({'; '.join(fx[:4])}) – brennt neu ein")
                    wfin, nur_ut = w_neu, True
                    f = burn(n, c["cut"], wfin, card_title, c["ranges"], c["kept"])
                else:
                    log("Schluss-Abnahme: Für diesen Befund gibt es keine gezielte Reparatur")
                    break
            if best and not bestanden:  # letzter Versuch: vorsichtige Sicherheits-Fassung (mehr Luft, keine optionalen Kürzungen)
                step(0.95, "Schneidet eine vorsichtige Sicherheits-Fassung und vergleicht …")
                KONSERVATIV[0] = True
                try:
                    c_b = make_cut(n, fix_bounds(best["c"]["m"]), step)
                finally:
                    KONSERVATIV[0] = False
                if c_b:
                    QUIZ.pop(n, None)
                    w_b, _ = reapply_fixes(c_b["words_out"], revs0)
                    f_b = burn(n, c_b["cut"], w_b, card_title, c_b["ranges"], c_b["kept"])
                    end_b, ok_b, schwer_b, w_b2, fx_b = abnehmen(c_b, f_b, w_b, "Sicherheits-Fassung")
                    if end_b:
                        if fx_b:
                            w_b = w_b2
                            f_b = burn(n, c_b["cut"], w_b, card_title, c_b["ranges"], c_b["kept"])
                        note_b = float(end_b["note"])
                        key_b = note_b - 0.5 * len(schwer_b)
                        verlauf.append({"note": note_b, "schnittfehler": len(schwer_b), "untertitel": len(fx_b), "sicherheits_fassung": True})
                        # Messlatte als Gegengewicht (12.10.): Im Test gewann die Sicherheits-Fassung mit Note 8 gegen 6 – der Prüfer
                        # hatte nur das (nicht behebbare) abrupte Ende bemängelt; die Sicherheits-Fassung hatte aber den Stotterer
                        # „auch den, ähm, auch den“ und zwei „ähm“ wieder drin. Sie gewinnt nur, wenn sie hörbar nicht schlechter ist.
                        objektiv = True
                        try:
                            import messlatte
                            messlatte.WHISPER = WHISPER
                            mo_a = messlatte.schnitt(f_best, [[w_[0], w_[1], w_[2]] for w_ in best["wfin"]])
                            mo_b = messlatte.schnitt(f_b, [[w_[0], w_[1], w_[2]] for w_ in w_b])
                            objektiv = mo_b["fehler_je_100"] <= mo_a["fehler_je_100"] + 0.5
                            log(f"Messlatte: bisher beste Fassung {mo_a['fehler_je_100']} Fehler je 100 Wörter, Sicherheits-Fassung {mo_b['fehler_je_100']}"
                                + ("" if objektiv else " – Sicherheits-Fassung ist hörbar schlechter, bleibt draußen"))
                        except Exception as e_m:  # noqa: BLE001
                            log(f"Messlatte nicht möglich ({e_m})")
                        log(f"Schluss-Abnahme Sicherheits-Fassung: Note {note_b:.0f}/10, {len(schwer_b)} Schnittfehler"
                            + (" – besser, wird genommen" if key_b > best["key"] and objektiv else " – nicht besser"))
                        if key_b > best["key"] and objektiv:
                            shutil.copyfile(f_b, f_best)
                            best = {"key": key_b, "c": c_b, "wfin": w_b, "note": note_b, "end": end_b, "runde": "sicher", "sf": len(schwer_b),
                                    "ok": note_b >= ABNAHME_NOTE and not schwer_b, "stats": {k_: STATS.get(k_) for k_ in stat_keys}}
                            bestanden = best["ok"]
            if best:
                os.replace(f_best, f)  # ausgeliefert wird immer die beste abgenommene Fassung
                c, wfin = best["c"], best["wfin"]
                for k_, v_ in best["stats"].items():
                    if v_ is None:
                        STATS.pop(k_, None)
                    else:
                        STATS[k_] = v_
                pruefung = {"note": best["note"], "bestanden": bool(bestanden), "fassung": best["runde"], "runden": verlauf, "schnittfehler": best.get("sf", 0),
                            "probleme": probs_of(best["end"])[:6]}
                STATS["schluss_abnahme"] = pruefung
                log(f"Schluss-Abnahme: ausgeliefert wird Fassung {best['runde']} mit Note {best['note']:.0f}/10 – "
                    + ("bestanden" if bestanden else "NICHT bestanden (beste erreichbare Fassung nach allen Runden)"))
        except Exception as e_:  # noqa: BLE001
            KONSERVATIV[0] = False
            log(f"Schluss-Abnahme nicht möglich ({e_}) – das Video bleibt, wie es ist")
            if os.path.exists(f_best) and not os.path.exists(f):
                os.replace(f_best, f)
        if os.path.exists(f_best):
            os.remove(f_best)
        try:   # Messlatte des ausgelieferten Videos ins Protokoll – so sieht man bei JEDEM Auftrag, wie sauber der Schnitt ist
            import messlatte
            messlatte.WHISPER = WHISPER
            mo_ = messlatte.schnitt(f, [[w_[0], w_[1], w_[2]] for w_ in wfin])
            # „verschluckt“: Wörter, die drin sein SOLLTEN, aber schon in der Hörprobe des Schnitts nicht zu hören waren – die
            # stehen nicht mehr im Untertitel und fielen deshalb durch die Messlatte (Auto-Clip 12.10.: „keine Kunden haben“).
            mo_["verschluckt"] = [w_ for w_ in (STATS.get("missing") or []) if not messlatte.nur_zahlen([messlatte.norm(w_)]) and len(messlatte.norm(w_)) >= 5 and not w_.isupper()]
            _schon = {x_ for f_ in mo_["fehlt"] for x_ in f_[1].split()}
            mo_["verschluckt"] = [w_ for w_ in mo_["verschluckt"] if messlatte.norm(w_) not in _schon]
            mo_["fehler"] += len(mo_["verschluckt"])
            mo_["fehler_je_100"] = round(100 * mo_["fehler"] / max(1, mo_["woerter"] + len(mo_["verschluckt"])), 1)
            STATS["messlatte"] = {k_: (v_[:6] if isinstance(v_, list) else v_) for k_, v_ in mo_.items()}
            log(f"Messlatte: {mo_['fehler']} Fehler auf {mo_['woerter']} Wörter ({mo_['fehler_je_100']} je 100)"
                + (f", davon {len(mo_['verschluckt'])} verschluckt: {', '.join(mo_['verschluckt'][:6])}" if mo_["verschluckt"] else ""))
        except Exception as e_m:  # noqa: BLE001
            log(f"Messlatte nicht möglich ({e_m})")
    if not A.keep_work and os.path.exists(c["cut"]):
        os.remove(c["cut"])
    # Untergrenze (Inhaber 10./11.10.: „Wenn da ein Fehler ist, gibst du das Video nicht raus“): Ist die beste erreichbare
    # Fassung nach allen Runden immer noch klar mangelhaft – Prüfer-Note ≤ 4 mit mindestens zwei Schnittfehlern ODER mehr als
    # 8 hörbare Fehler je 100 Wörter –, wird sie NICHT ausgeliefert. Der Kunde bekommt dann seine Credits zurück statt eines
    # schlechten Videos; das Protokoll hält den Fall fest (Lernmaterial). VELOX_UNTERGRENZE=0 schaltet das ab.
    if os.environ.get("VELOX_UNTERGRENZE", "1") == "1" and pruefung and not pruefung.get("bestanden"):
        _ml = (STATS.get("messlatte") or {}).get("fehler_je_100", 0)
        if (pruefung["note"] <= float(os.environ.get("VELOX_MIN_NOTE", "4")) and pruefung.get("schnittfehler", 0) >= 2) or _ml > 8:
            log(f"Clip {n}: NICHT ausgeliefert – Note {pruefung['note']:.0f}/10, {pruefung.get('schnittfehler', 0)} Schnittfehler, "
                f"Messlatte {_ml} je 100 Wörter. Probleme: " + "; ".join(str(p_)[:80] for p_ in (pruefung.get("probleme") or [])[:3]))
            STATS.setdefault("abgelehnt", []).append({"clip": n, "note": pruefung["note"], "messlatte": _ml})
            ABGELEHNT.append(n)
            if not A.keep_work and os.path.exists(f):
                os.remove(f)
            continue
    try:   # Wortliste der AUSGELIEFERTEN Fassung (burn schreibt sie bei jedem Einbrennen – die letzte war nicht immer die gewählte)
        json.dump([[w_[0], round(float(w_[1]), 3), round(float(w_[2]), 3)] for w_ in (wfin if not A.no_review else c["words_out"])],
                  open(f[:-4] + ".woerter.json", "w"), ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass
    words, kept, secs = c["words"], c["kept"], c["secs"]
    orig = words[-1]["e"] - words[0]["s"]
    log(f"Clip {n}: {orig:.1f} s → {secs} s, {len(c['ranges'])} Abschnitte, {len(words) - len(kept)} Wörter entfernt")
    step(0.95, "Letzter Qualitäts-Check …")
    quality_check(f, words, kept, orig, secs)
    results.append({"file": f, "title": title[:80], "seconds": secs, "songs": suggest_songs(kept, card_title),
                    "post": post_text(kept, card_title), "pruefung": pruefung})

if not A.keep_work:
    os.remove(wav)
    for _f in __import__("glob").glob(os.path.join(A.out, "broll-*.mp4")):  # in Resolve korrigierte Zwischenbilder
        os.remove(_f)
if not results and ABGELEHNT:
    sys.exit("Aus dieser Aufnahme konnten wir kein Video in guter Qualität schneiden – der Ton oder der Inhalt war an zu vielen "
             "Stellen unklar. Versuche es gern mit einer anderen Aufnahme")
if not results:
    sys.exit("Es konnten keine Clips erstellt werden.")
ki.require_or_exit("KI-Abnahme")
print(json.dumps({"clips": results}, ensure_ascii=False), flush=True)
