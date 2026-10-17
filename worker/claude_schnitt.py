"""Velox Cut – Claude-Schnitt (seit 13.10.2026).

Inhaber 13.10.: „Claude soll Augen, Ohren und der Analysator für die Videos sein – nicht mehr Google AI Studio.“
Hier entscheidet kein kleines KI-Modell in Einzelfragen. Claude sieht sich das ganze Material an, hört jeden Clip wortgenau ab,
schreibt EINEN Schnittplan (plan.json) und dieses Programm baut daraus das Video. Kein Gemini, kein Guthaben.

  1. hoeren   <ordner> <arbeitsordner>      – jeden Clip abhören (Whisper lokal, Wort für Wort), Standbilder ziehen;
                                              schreibt  material.md  (was in welchem Clip wann gesagt wird) und Kontaktbögen
  2. Claude liest material.md + Kontaktbögen und schreibt  plan.json  (siehe PLAN unten)
  3. bauen    <plan.json>                   – Video rendern
  4. pruefen  <video> <arbeitsordner>       – Übersichtsbögen + Lautheit zum Selbst-Ansehen vor der Auslieferung

PLAN (alle Zeiten sind Sekunden IM ROHCLIP – das Programm rechnet selbst um und setzt die Schnittkanten in die Ruhe neben dem Wort):
{
 "ordner": "/…/raw", "ziel": "/…/final.mp4", "arbeit": "/…/edit",
 "titel": "Cheesecake hat geöffnet", "post": {"text": "…", "hashtags": ["#mannheim", …]},   fürs Studio (freigeben.mjs übernimmt sie)
 "stufe": "clean" | "viral" | "premium",    Stil des Auftrags (Regel 136): clean ohne Karten/Töne, viral mit, premium zusätzlich Kamerafahrten + Fortschrittslinie
 "akzent": "#F6D660",                       Farbe des Kunden (Linien, Haken, Schlagwort in viral/premium)
 "untertitel": {"stil": "auto", "farbe": "#FFD400"} | fehlt,   Wunsch des Kunden aus dem Studio (caption_style / caption_color); auto = Stil der Stufe
 "logo": "/voller/pfad/logo.png" | fehlt,   Logo des Kunden, klein oben links
 "musik": "joyful.mp3" | "/voller/pfad.mp3" | null,   aus schnitt-maschine/musik oder material/MUSIK/…; null = ohne Musik (dann auch keine Schluss-Montage)
 ("zoom" je Sprechteil weglassen – der Ausschnitt wechselt dann von selbst; "stabil": false / "klarheit": false schalten die Prüfungen ab)
 "sprechteile": [ {"clip": "C4459.MP4", "von": 12.10, "bis": 16.14, "zoom": 1.0,
                   "ersetzen": {"Erdbeerwäsche": "Erdbeerbecher"},   Schreibweise berichtigen (Wort im Transkript → Untertitel)
                   "gross": ["London,", "Mannheim."] } … ],         Schlüsselwörter groß; fehlt der Eintrag, wählt das Programm
 "einschuebe": [ {"teil": 0, "ab": 14.34, "bis": null, "clip": "C4460.MP4", "start": 17.0, "tempo": 0.6} … ],   Bild über dem Gesprochenen
 "montage": {"schnitt_s": 0.75, "clips": [["C4446.MP4", 6.0], …], "ende": ["C4451.MP4", 5.0], "ende_s": 2.9,
             "schild": ["JETZT GEÖFFNET", "C1, 8 · Mannheim"]},
 "grafiken": [ {"art": "emoji", "zeichen": "🇬🇧", "teil": 0, "ab": 13.30},
               {"art": "preis", "oben": "Klassiker", "gross": "1,99 €", "teil": 1, "ab": 5.28, "bis": 6.57},
               {"art": "marke", "zeichen": "✨", "text": "Special", "teil": 1, "ab": 6.86},
               {"art": "ort", "zeile1": "C1, 8 · Mannheim", "zeile2": "Jetzt geöffnet", "teil": 3, "ab": 46.70},
               {"art": "liste", "titel": "Versicherungskarte", "teil": 2, "punkte": [["Beim Tierarzt vorgelegt", 31.2], …]},
               {"art": "pfeil", "text": "Blueberry Cheesecake", "x": 620, "y": 900, "teil": 1, "ab": 7.9, "bis": 8.7},   zeigt auf das Produkt: (x, y) = Punkt im fertigen 1080×1920-Bild
               {"art": "pille", "text": "catcare24", "teil": 2, "ab": 30.0} ]      „bis“ fehlt = bis zum Ende des Sprechteils
}
"""
import json
import time, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MASCH = os.environ.get("VELOX_MASCHINE", os.path.join(HERE, "..", "..", "schnitt-maschine"))
FONTS = os.path.join(MASCH, "fonts")
FPS = 30
GRADE = "eq=contrast=1.05:saturation=1.10:gamma=1.03"
VIDEO = (".mp4", ".mov", ".m4v", ".mts", ".mkv")
norm = lambda s: re.sub(r"[^\wäöüß]", "", str(s).lower())


def run(cmd, ok=False):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 and not ok:
        sys.exit("ffmpeg: " + (r.stderr or r.stdout)[:1500])
    return r


def dauer(p):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p]).stdout.strip() or 0)


# ───────────────────────────── 1. Ohren und Augen ─────────────────────────────
def transkript(clip, arbeit):
    """Wortgenaues Transkript eines Clips (zwischengespeichert). [] wenn niemand spricht."""
    ziel = os.path.join(arbeit, "transcripts", os.path.splitext(os.path.basename(clip))[0] + ".json")
    if os.path.exists(ziel):
        return json.load(open(ziel))
    os.makedirs(os.path.dirname(ziel), exist_ok=True)
    vol = re.search(r"mean_volume: ([-\d.]+)", run(["ffmpeg", "-v", "info", "-t", "90", "-i", clip, "-vn", "-af", "volumedetect", "-f", "null", "-"], ok=True).stderr)
    ws = []
    # Erster Test (Golden Thai, 13.10.): zwei sehr leise aufgenommene Sprech-Clips (−50 dB im Mittel) galten als „niemand spricht“.
    # Abgehört wird deshalb alles, was nicht praktisch stumm ist; leise Aufnahmen werden vorher angehoben (nur zum Abhören).
    if vol and float(vol.group(1)) > -70.0:
        import mlx_whisper
        wav = ziel[:-5] + ".wav"
        leise = float(vol.group(1)) < -34.0
        run(["ffmpeg", "-v", "error", "-y", "-i", clip, "-vn", "-ac", "1", "-ar", "16000", "-af",
             "aresample=async=1:first_pts=0" + (f",volume={min(30.0, -24.0 - float(vol.group(1))):.1f}dB,alimiter=limit=0.9" if leise else ""), wav])
        r = mlx_whisper.transcribe(wav, path_or_hf_repo=os.environ.get("VELOX_WHISPER", "mlx-community/whisper-large-v3-turbo"), word_timestamps=True,
                                   condition_on_previous_text=False, temperature=0.0, hallucination_silence_threshold=2.0, initial_prompt="Ähm, also, äh, ich wollte … ähm, ja. Genau.")
        os.remove(wav)
        ws = [{"t": x["word"].strip(), "s": round(float(x["start"]), 2), "e": round(float(x["end"]), 2), "p": round(float(x.get("probability", 1.0)), 2)}
              for sg in r["segments"] if sg.get("no_speech_prob", 0) < 0.8 for x in sg.get("words", []) if x["word"].strip()]
        # NACHHÖREN (Golden Thai Halal, 13.10.): „Halal Thai ist[2,7 s] kein[2,4 s] Trend,[2,6 s]“ – so lang ist kein Wort. Dahinter
        # steckten drei Anläufe desselben Satzes, die die Erkennung zu einem verschmolzen hatte. Jede Stelle mit einem unmöglich
        # langen Wort wird einzeln neu abgehört; das Ergebnis ersetzt die Wörter dieser Stelle.
        i = 0
        while i < len(ws):
            if ws[i]["e"] - ws[i]["s"] > 1.1:
                j = i
                while j + 1 < len(ws) and (ws[j + 1]["e"] - ws[j + 1]["s"] > 1.1 or ws[j + 1]["s"] - ws[j]["e"] < 0.05) and j - i < 12:
                    j += 1
                a0 = max(0.0, (ws[i - 1]["e"] if i else 0.0) - 0.1)
                b0 = (ws[j + 1]["s"] if j + 1 < len(ws) else ws[j]["e"]) + 0.1
                snip = ziel[:-5] + ".nach.wav"
                run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a0:.2f}", "-t", f"{b0 - a0:.2f}", "-i", clip, "-vn", "-ac", "1", "-ar", "16000", "-af",
                     (f"volume={min(30.0, -24.0 - float(vol.group(1))):.1f}dB,alimiter=limit=0.9" if leise else "anull"), snip])
                r2 = mlx_whisper.transcribe(snip, path_or_hf_repo=os.environ.get("VELOX_WHISPER", "mlx-community/whisper-large-v3-turbo"), word_timestamps=True,
                                            condition_on_previous_text=False, temperature=0.0)
                os.remove(snip)
                neu = [{"t": x["word"].strip(), "s": round(a0 + float(x["start"]), 2), "e": round(a0 + float(x["end"]), 2), "p": round(float(x.get("probability", 1.0)), 2)}
                       for sg in r2["segments"] for x in sg.get("words", []) if x["word"].strip()]
                neu = [w for w in neu if w["s"] >= (ws[i - 1]["e"] if i else 0.0) - 0.02 and w["e"] <= (ws[j + 1]["s"] if j + 1 < len(ws) else 1e9) + 0.02]
                if neu and max(w["e"] - w["s"] for w in neu) < max(w["e"] - w["s"] for w in ws[i:j + 1]):
                    ws[i:j + 1] = neu
                    i += len(neu)
                    continue
            i += 1
        # LÜCKEN NACHHÖREN (Golden Thai Papaya, 13.10.): „…die eine Frucht, [1,3 s Lücke] deine Verdauung…“ – in der Lücke wurde
        # „die in 24 Stunden“ gesprochen, die Erkennung hatte es verschluckt, im fertigen Video fehlten drei Wörter. Jede Pause
        # zwischen zwei Wörtern, in der es nicht still ist, wird mit je drei Wörtern Umgebung neu abgehört; was man dabei sicher
        # IN der Lücke hört, wird eingefügt.
        i = 0
        while i + 1 < len(ws):
            if ws[i + 1]["s"] - ws[i]["e"] >= 0.7 and ws[i + 1]["s"] - ws[i]["e"] <= 6.0:
                lv = re.search(r"mean_volume: ([-\d.]+)", run(["ffmpeg", "-v", "info", "-ss", f"{ws[i]['e'] + 0.1:.2f}", "-t", f"{ws[i + 1]['s'] - ws[i]['e'] - 0.2:.2f}", "-i", clip,
                                                              "-vn", "-af", "volumedetect", "-f", "null", "-"], ok=True).stderr)
                if lv and float(lv.group(1)) > float(vol.group(1)) - 8.0:
                    u, o = max(0, i - 2), min(len(ws) - 1, i + 3)
                    a0, b0 = ws[u]["s"], ws[o]["e"]
                    snip = ziel[:-5] + ".nach.wav"
                    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, a0 - 0.15):.2f}", "-t", f"{b0 - a0 + 0.3:.2f}", "-i", clip, "-vn", "-ac", "1", "-ar", "16000", "-af",
                         (f"volume={min(30.0, -24.0 - float(vol.group(1))):.1f}dB,alimiter=limit=0.9" if leise else "anull"), snip])
                    r2 = mlx_whisper.transcribe(snip, path_or_hf_repo=os.environ.get("VELOX_WHISPER", "mlx-community/whisper-large-v3-turbo"), word_timestamps=True,
                                                condition_on_previous_text=False, temperature=0.0)
                    os.remove(snip)
                    neu = [{"t": x["word"].strip(), "s": round(max(0.0, a0 - 0.15) + float(x["start"]), 2), "e": round(max(0.0, a0 - 0.15) + float(x["end"]), 2),
                            "p": round(float(x.get("probability", 1.0)), 2)} for sg in r2["segments"] for x in sg.get("words", []) if x["word"].strip()]
                    # Übernommen werden nur die Wörter, deren Mitte IN der Lücke liegt – die schon bekannten Wörter davor und danach
                    # bleiben, wie sie sind (ein Austausch der ganzen Stelle brachte am Rand angeschnittene Wörter doppelt).
                    neu = [w for w in neu if w["e"] - w["s"] <= 1.1 and ws[i]["e"] - 0.05 < (w["s"] + w["e"]) / 2 < ws[i + 1]["s"] + 0.05 and w["p"] >= 0.5]
                    if neu and sum(w["p"] for w in neu) / len(neu) >= 0.75:
                        neu[0]["s"] = max(neu[0]["s"], ws[i]["e"])
                        ws[i + 1]["s"] = min(max(ws[i + 1]["s"], neu[-1]["e"]), ws[i + 1]["e"] - 0.08)
                        neu[-1]["e"] = min(neu[-1]["e"], ws[i + 1]["s"])
                        ws[i + 1:i + 1] = neu
                        i += len(neu)
            i += 1
    json.dump(ws, open(ziel, "w"), ensure_ascii=False)
    return ws


def hoeren(ordner, arbeit):
    from PIL import Image, ImageDraw, ImageFont
    os.makedirs(os.path.join(arbeit, "thumbs"), exist_ok=True)
    clips = sorted(f for f in os.listdir(ordner) if f.lower().endswith(VIDEO) and not f.startswith("._"))
    zeilen, zellen = [f"# Material aus {ordner}  ({len(clips)} Clips)\n",
                      "Zeiten in Sekunden im Rohclip. `[von-bis]` = zusammenhängend gesprochen (Pause ≥ 0,5 s trennt). (leise/unsicher) = Erkennung unsicher.\n"], []
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 15)
    for n, f in enumerate(clips):
        p = os.path.join(ordner, f)
        d = dauer(p)
        ws = transkript(p, arbeit)
        zeilen.append(f"\n## {f}  ({d:.1f} s){'' if ws else '  – niemand spricht (Bildmaterial)'}")
        cur = []
        for w in ws + [None]:
            if w is None or (cur and w["s"] - cur[-1]["e"] >= 0.5):
                if cur:
                    uns = sum(1 for x in cur if x.get("p", 1) < 0.5)
                    zeilen.append(f"  [{cur[0]['s']:6.2f}-{cur[-1]['e']:6.2f}] " + " ".join(x["t"] for x in cur) + ("   (leise/unsicher)" if uns > len(cur) / 3 else ""))
                cur = []
            if w:
                cur.append(w)
        ims = []
        for q in (0.2, 0.5, 0.8):
            t = os.path.join(arbeit, "thumbs", f"{os.path.splitext(f)[0]}_{q}.jpg")
            if not os.path.exists(t):
                run(["ffmpeg", "-v", "error", "-y", "-ss", f"{d * q:.2f}", "-i", p, "-frames:v", "1", "-vf", "scale=-2:284", "-q:v", "6", t], ok=True)
            if os.path.exists(t):
                im = Image.open(t)
                if im.width > im.height:
                    im = im.crop(((im.width - im.height * 9 // 16) // 2, 0, (im.width + im.height * 9 // 16) // 2, im.height))
                ims.append(im.resize((100, 178)))
        if ims:
            c = Image.new("RGB", (304, 200), (20, 20, 20))
            for i, im in enumerate(ims):
                c.paste(im, (i * 100, 22))
            ImageDraw.Draw(c).text((3, 2), f"{os.path.splitext(f)[0][-9:]} {d:.0f}s {'SPRICHT' if ws else ''}", font=font, fill=(255, 230, 120) if ws else (255, 255, 255))
            zellen.append(c)
        print(f"{n + 1}/{len(clips)} {f}: {len(ws)} Wörter", flush=True)
    open(os.path.join(arbeit, "material.md"), "w").write("\n".join(zeilen) + "\n")
    for n in range(0, len(zellen), 24):
        teil = zellen[n:n + 24]
        bogen = Image.new("RGB", (6 * 304, ((len(teil) + 5) // 6) * 200), (0, 0, 0))
        for i, c in enumerate(teil):
            bogen.paste(c, ((i % 6) * 304, (i // 6) * 200))
        bogen.save(os.path.join(arbeit, f"bogen_{n // 24}.jpg"), quality=80)
    print("geschrieben:", os.path.join(arbeit, "material.md"), "+", (len(zellen) + 23) // 24, "Kontaktbögen")


# ───────────────────────────── 2. Schnittkanten in die Ruhe legen ─────────────────────────────
_PEGEL = {}


def pegel(clip):
    """Lautstärke in 10-ms-Schritten (dB)."""
    if clip not in _PEGEL:
        import numpy as np
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", clip, "-vn", "-ac", "1", "-ar", "16000", "-af", "aresample=async=1:first_pts=0", "-f", "s16le", "-"],
                             capture_output=True).stdout
        x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
        _PEGEL[clip] = [20 * float(np.log10(np.sqrt((x[i:i + 320] ** 2).mean()) + 1e-7)) for i in range(0, max(1, len(x) - 320), 160)]
    return _PEGEL[clip]


def einsatz(clip, w):
    """ECHTER WORTBEGINN (Inhaber 17.10.: „bei den Aufzählungen hört man davor noch den Kameramann reden – das sage ich dir zum
    wievielten Mal“). Die Erkennung dehnt das erste Wort eines Clips oft über alles davor („Oder“ 0,0–1,24 s) – darin steckt dann
    die Stimme hinter der Kamera. Die Person vor der Kamera trägt das Mikro und ist deutlich lauter: Der Schnitt beginnt erst dort,
    wo der letzte laute Abschnitt des Wortes anfängt (nach mindestens 0,08 s, die mehr als 14 dB leiser waren)."""
    if w["e"] - w["s"] <= 0.55:
        return w["s"]
    lv = pegel(clip)
    i0, i1 = int(w["s"] / 0.01), int(w["e"] / 0.01)
    seg = lv[i0:i1]
    if len(seg) < 10:
        return w["s"]
    schwelle = max(seg) - 14.0
    # Test 17.10. (Papaya): „Stopp!“ war kurz und laut, danach Stille bis zum Wortende – die Stille AM ENDE ist keine Lücke vor dem
    # Sprecher. Deshalb erst vom Ende her bis zum letzten lauten Abschnitt gehen, dann dessen Anfang suchen.
    k = len(seg) - 1
    while k > 0 and seg[k] < schwelle:
        k -= 1
    leise = 0
    while k > 0:
        if seg[k] < schwelle:
            leise += 1
            if leise >= 8:
                return round((i0 + k + leise) * 0.01, 2)
        else:
            leise = 0
        k -= 1
    return w["s"]


def kante(clip, t, anfang, grenze):
    """Schnittkante neben das Wort legen – „nicht zu früh, nicht zu spät“: am Anfang die leiseste Stelle in den 0,16 s VOR dem
    Wort (nie vor `grenze` = Ende des Wortes davor), am Ende die leiseste Stelle in den 0,22 s NACH dem Wort (nie nach `grenze`)."""
    lv = pegel(clip)
    a, b = (max(grenze + 0.02, t - 0.16), t - 0.02) if anfang else (t + 0.04, min(grenze - 0.02, t + 0.22))
    if b <= a:
        return max(0.0, t - 0.05) if anfang else t + 0.08
    i0, i1 = int(a / 0.01), max(int(a / 0.01) + 1, int(b / 0.01))
    seg = lv[i0:i1]
    if not seg:
        return a if anfang else b
    # am Anfang möglichst spät (nah am Wort), am Ende möglichst früh – aber erst in echter Ruhe
    ruhe = min(seg) + 3.0
    idx = [i for i, v in enumerate(seg) if v <= ruhe]
    k = (idx[-1] if anfang else idx[0])
    return (i0 + k) * 0.01 + 0.005


# ───────────────────────────── 3. Grafiken ─────────────────────────────
def _pil():
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    return Image, ImageDraw, ImageFilter, ImageFont


F6, F8 = os.path.join(FONTS, "Poppins-normal-600.ttf"), os.path.join(FONTS, "Poppins-normal-800.ttf")
_EMO = []


def emoji(ch, px):
    Image, ImageDraw, _, ImageFont = _pil()
    if not _EMO:
        _EMO.append(ImageFont.truetype("/System/Library/Fonts/Apple Color Emoji.ttc", 160))
    im = Image.new("RGBA", (220, 220), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((30, 30), ch, font=_EMO[0], embedded_color=True)
    box = im.getbbox()
    return im.crop(box).resize((px, px), Image.LANCZOS) if box else Image.new("RGBA", (px, px), (0, 0, 0, 0))


GLAS = (34, 34, 40, 96)   # Inhaber 13.10.: „schlicht, clean, weiß … Glas, durchsichtiger Hintergrund – nicht weiß, nicht farbig, nichts, was stört“


def karte(w, h, r, fill=None):
    """Glas-Karte: leicht getöntes, durchsichtiges Feld mit feinem hellem Rand und Lichtkante oben – weiße Schrift darauf."""
    Image, ImageDraw, ImageFilter, _ = _pil()
    P = 40
    im = Image.new("RGBA", (w + 2 * P, h + 2 * P), (0, 0, 0, 0))
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((P, P + 8, P + w, P + h + 8), r, fill=(0, 0, 0, 70))
    im = Image.alpha_composite(im, sh.filter(ImageFilter.GaussianBlur(14)))
    glas = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(glas)
    d.rounded_rectangle((P, P, P + w, P + h), r, fill=GLAS, outline=(255, 255, 255, 120), width=2)
    d.rounded_rectangle((P + 3, P + 3, P + w - 3, P + h // 2), max(4, r - 3), fill=(255, 255, 255, 16))   # Lichtkante
    return Image.alpha_composite(im, glas), P


def grafik(g, akzent, pfad):
    """Zeichnet eine Grafik, gibt (Breite, Höhe) der sichtbaren Karte zurück (ohne Schattenrand von 40 px)."""
    Image, ImageDraw, ImageFilter, ImageFont = _pil()
    acc = tuple(int(akzent.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
    art = g["art"]
    if art == "emoji":
        im, P = karte(108, 108, 30)
        im.alpha_composite(emoji(g["zeichen"], 68), (P + 20, P + 20))
        wh = (108, 108)
    elif art in ("marke", "pille"):
        f = ImageFont.truetype(F6, 36)
        tw = int(f.getlength(g["text"]))
        hat = bool(g.get("zeichen"))
        w = tw + (96 if hat else 60)
        im, P = karte(w, 70, 35)
        if hat:
            im.alpha_composite(emoji(g["zeichen"], 36), (P + 20, P + 17))
        ImageDraw.Draw(im).text((P + (70 if hat else 30), P + 35), g["text"], font=f, fill=(255, 255, 255, 255), anchor="lm")
        wh = (w, 70)
    elif art == "pfeil":      # zeigt auf etwas im Bild: Glas-Schild mit Text, darunter ein Finger nach unten
        f = ImageFont.truetype(F6, 38)
        w = int(f.getlength(g["text"])) + 64
        im, P = karte(w, 74, 37)
        ImageDraw.Draw(im).text((P + w // 2, P + 37), g["text"], font=f, fill=(255, 255, 255, 255), anchor="mm")
        gz = Image.new("RGBA", (im.size[0], im.size[1] + 70), (0, 0, 0, 0))
        if g.get("_von_unten"):      # Schild unter dem Punkt, Finger zeigt nach oben
            gz.alpha_composite(emoji("👆", 64), (P + w // 2 - 32, P + 2))
            gz.alpha_composite(im, (0, 76))
        else:
            gz.alpha_composite(im, (0, 0))
            gz.alpha_composite(emoji("👇", 64), (P + w // 2 - 32, P + 80))
        im = gz
        wh = (w, 150)
    elif art == "preis":
        f2 = ImageFont.truetype(F8, 84)
        w = max(330, int(f2.getlength(g["gross"])) + 80)
        im, P = karte(w, 170, 34)
        d = ImageDraw.Draw(im)
        d.text((P + w // 2, P + 40), g.get("oben", ""), font=ImageFont.truetype(F6, 30), fill=(255, 255, 255, 200), anchor="mm")
        d.text((P + w // 2, P + 108), g["gross"], font=f2, fill=(255, 255, 255, 255), anchor="mm")
        d.rounded_rectangle((P + 40, P + 152, P + w - 40, P + 158), 3, fill=acc)
        wh = (w, 170)
    elif art == "ort":
        f1, f2 = ImageFont.truetype(F8, 40), ImageFont.truetype(F6, 30)
        w = max(int(f1.getlength(g["zeile1"])), int(f2.getlength(g.get("zeile2", "")))) + 150
        im, P = karte(w, 124, 30)
        d = ImageDraw.Draw(im)
        im.alpha_composite(emoji(g.get("zeichen", "📍"), 60), (P + 26, P + 32))
        d.text((P + 108, P + 44), g["zeile1"], font=f1, fill=(255, 255, 255, 255), anchor="lm")
        d.text((P + 108, P + 88), g.get("zeile2", ""), font=f2, fill=(255, 255, 255, 200), anchor="lm")
        wh = (w, 124)
    elif art == "liste":      # eine Stufe der Haken-Liste: Titel + die ersten n Punkte
        f1, f2 = ImageFont.truetype(F6, 30), ImageFont.truetype(F6, 36)
        pk = g["punkte"][:g["n"]]
        w = min(760, max([int(f2.getlength(p[0])) for p in g["punkte"]] + [int(f1.getlength(g.get("titel", "")))]) + 130)
        h = 74 + 58 * len(g["punkte"])
        im, P = karte(w, h, 30)
        d = ImageDraw.Draw(im)
        d.text((P + 30, P + 36), g.get("titel", ""), font=f1, fill=(255, 255, 255, 190), anchor="lm")
        for i, p in enumerate(pk):
            y = P + 96 + 58 * i
            d.ellipse((P + 28, y - 17, P + 62, y + 17), fill=(52, 199, 89, 255))
            d.line((P + 37, y, P + 43, y + 7, P + 54, y - 7), fill=(255, 255, 255, 255), width=4)
            d.text((P + 80, y), p[0], font=f2, fill=(255, 255, 255, 255), anchor="lm")
        wh = (w, h)
    elif art == "schild":
        f1, f2 = ImageFont.truetype(F8, 92), ImageFont.truetype(F6, 44)
        while f1.getlength(g["zeile1"]) > 980 and f1.size > 50:
            f1 = ImageFont.truetype(F8, f1.size - 4)
        im = Image.new("RGBA", (1080, 420), (0, 0, 0, 0))
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ds = ImageDraw.Draw(sh)
        ds.text((540, 150), g["zeile1"], font=f1, fill=(0, 0, 0, 200), anchor="mm")
        ds.text((540, 258), g.get("zeile2", ""), font=f2, fill=(0, 0, 0, 200), anchor="mm")
        im = Image.alpha_composite(im, sh.filter(ImageFilter.GaussianBlur(14)))
        d = ImageDraw.Draw(im)
        d.text((540, 150), g["zeile1"], font=f1, fill=(255, 255, 255, 255), anchor="mm")
        d.rounded_rectangle((300, 208, 780, 214), 3, fill=acc)
        d.text((540, 258), g.get("zeile2", ""), font=f2, fill=(255, 255, 255, 255), anchor="mm")
        im.save(pfad)
        return (1080, 420), 0
    else:
        sys.exit(f"Grafik-Art unbekannt: {art}")
    im.save(pfad)
    return wh, 40


# ───────────────────────────── 4. Bauen ─────────────────────────────
def bauen(plan_pfad):
    import numpy as np
    P = json.load(open(plan_pfad))
    ORD, ARB, ZIEL = P["ordner"], P["arbeit"], P["ziel"]
    AKZ = P.get("akzent", "#F6D660")
    CL, GR = os.path.join(ARB, "clips"), os.path.join(ARB, "grafik")
    os.makedirs(CL, exist_ok=True)
    os.makedirs(GR, exist_ok=True)
    src = lambda c: os.path.join(ORD, c)
    W = lambda c: transkript(src(c), ARB)
    # STUFEN (Inhaber 13.10.: „es müssen immer Unterschiede erkennbar sein zwischen Clean, Viral und Premium – aber auch Clean ist
    # schon hochwertig: keine Versprecher, gute Cuts, alles stabilisiert, alles gut gegradet“). Schnitt, Farbe, Stabilisierung und
    # Untertitel bekommt JEDE Stufe; die Stufen unterscheiden sich in dem, was dazukommt:
    #   clean   – ruhig: kleine weiße Untertitel, Schlagwort kursiv; leichter Bildwechsel am Schnitt; keine Karten, keine Töne
    #   viral   – lebendig: stärkerer Bildwechsel, Heranfahren beim Aufhänger, Glas-Karten und Emojis, Töne, Schlagwort in Akzentfarbe
    #   premium – alles aus viral, dazu Heranfahren in jedem Teil, Kino-Farbe mit leichter Randabdunklung, Fortschrittslinie
    STUFE = (P.get("stufe") or "viral").lower()
    if STUFE not in ("clean", "viral", "premium"):
        STUFE = "viral"
    PUNCH = {"clean": 1.06, "viral": 1.12, "premium": 1.12}[STUFE]
    _farbe = {}
    # DAVINCI RESOLVE (Inhaber 14.10.: „die Videos in DaVinci schneiden, editieren und color graden“). Ist Resolve offen und die
    # Brücke an, geht der fertig geschnittene Bildstrang (alle Sprechteile + Schluss-Montage, noch ohne Farbe) als EINE Timeline
    # nach Resolve: jede Aufnahme bekommt dort ihre eigene, an ihr gemessene Farbkorrektur (CDL auf Node 1), Resolve rendert, die
    # Timeline bleibt im Projekt „velox cut“ (Ordner der Velox-Aufträge) zum Nacharbeiten. Untertitel, Karten, Töne kommen danach
    # darüber (das kann Resolve in der kostenlosen Ausgabe per Skript nicht). Ist Resolve zu oder schlägt etwas fehl: dieselbe
    # Korrektur in ffmpeg. "resolve": false im Plan schaltet Resolve ab.
    RES = None
    if P.get("resolve", True):
        try:
            import resolve as _rsm
            RES = _rsm if _rsm.alive() else None
        except Exception:  # noqa: BLE001
            RES = None
    print("Farbe: " + ("DaVinci Resolve" if RES else "ffmpeg (Resolve nicht erreichbar)"), flush=True)

    def farbe(c):
        return "null" if RES else farbe_ff(c)

    def farbe_ff(c):
        """Farbkorrektur je Rohclip: gemessen (Schwarz-/Weißpunkt, Mitten am Gesicht, Weißabgleich, Sättigung – dieselbe Messung wie
        die Resolve-Stufe), angewendet in ffmpeg – läuft also auch, wenn Resolve zu ist. Jeder Clip bekommt SEINE Korrektur."""
        if c not in _farbe:
            try:
                import resolve as _rs
                cdl = _rs.auto_cdl(src(c), STUFE)
                S, O, Pw = ([float(x) for x in cdl[k].split()] for k in ("Slope", "Offset", "Power"))
                ch = lambda i: f"'255*pow(clip(val/255*{S[i]:.4f}+({O[i]:.4f})\\,0\\,1)\\,{Pw[i]:.4f})'"
                _farbe[c] = f"lutrgb=r={ch(0)}:g={ch(1)}:b={ch(2)},eq=saturation={float(cdl['Saturation']):.3f}"
                print(f"Farbe {c}: Slope {cdl['Slope']} · Power {Pw[0]:.2f} · Sättigung {cdl['Saturation']}", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"Farbe {c}: Messung nicht möglich ({e}) – Grundkorrektur", flush=True)
                _farbe[c] = GRADE
            if STUFE == "premium":
                _farbe[c] += ",vignette=PI/6"
        return _farbe[c]

    def stabil(c, a, d, name, fps_=FPS):
        """Stabilisierung (Inhaber 13.10.: „alles soll stabilisiert sein“) – nur wo das Bild wirklich zittert: Stativ-Aufnahmen
        bleiben unberührt (Stabilisieren würde dort nur Bildrand kosten). Liefert die stabilisierte Datei oder None."""
        if not P.get("stabil", True) or d < 0.8:
            return None
        ziel = f"{CL}/{name}_stab.mp4"
        wk = stabilisieren(src(c), a, d, ziel, fps_)
        if wk is None:
            return None
        # Geprüft 13.10.: künstlich verwackeltes Stativ-Bild 8,5 → 0,3. Wilde Handy-Schwenks (Küche, 2–5) wurden dagegen NICHT
        # ruhiger – dort ist die Bewegung gewollt oder zu groß. Bringt die Stabilisierung nicht mindestens 30 %, bleibt das Original.
        if wk[1] > wk[0] * 0.7:
            print(f"{name}: unruhiges Bild ({wk[0]:.2f}), Stabilisierung bringt nichts ({wk[1]:.2f}) → Original bleibt", flush=True)
            os.remove(ziel)
            return None
        print(f"{name}: Bild zittert ({wk[0]:.2f} px je Bild) → stabilisiert (danach {wk[1]:.2f})", flush=True)
        return ziel

    def res_projekt():
        """eigenes Projekt je Auftrag: „Velox <Datum Uhrzeit> <Auftragsordner>“ (Name wird beim ersten Bauen gemerkt, damit ein Neubau
        dasselbe Projekt nimmt); Auftrags-Projekte älter als 24 Stunden räumt das Programm selbst weg"""
        _pf = os.path.join(ARB, "resolve_projekt.txt")
        _pn = open(_pf).read().strip() if os.path.exists(_pf) else \
            f"Velox {time.strftime('%Y-%m-%d %H%M')} {os.path.basename(os.path.dirname(os.path.abspath(ARB).rstrip('/')))[:60]}"
        open(_pf, "w").write(_pn)
        _pr = RES.projekt(_pn)
        print(f"DaVinci Resolve: Projekt „{_pr['projekt']}“" + (" angelegt" if _pr.get("neu") else " geöffnet"), flush=True)
        try:
            _weg = RES.aufraeumen(24, _pn)
            if _weg:
                print(f"DaVinci Resolve: {len(_weg)} alte Auftrags-Projekte gelöscht", flush=True)
        except Exception:  # noqa: BLE001
            pass

    # SCHNITT IN RESOLVE (Inhaber 17.10.: „die Videos sollen in DaVinci geschnitten und editiert werden – Claude ist Augen, Ohren,
    # Kopf und prüft“). Ist Resolve erreichbar, schneidet RESOLVE die Sprechteile aus den Original-Clips (Timeline 9:16, Ausschnitt,
    # Stabilisierung, Farbe je Aufnahme, Rendern mit Ton). Danach kommen Kamerafahrten, Untertitel, Karten und Töne darüber.
    # Fällt Resolve aus, schneidet ffmpeg wie bisher. "resolve_schnitt": false im Plan = nur Farbe in Resolve.
    RESCUT = bool(RES) and P.get("resolve_schnitt", True)

    def klar(k):
        # VERSTÄNDLICHKEIT (Papaya-Video, 13.10.: Ich hatte die flüssigere frühere Fassung gewählt – einzeln und ohne Vorgabe abgehört
        # war sie genuschelt („Lungenweinigetron“, Sicherheit 0,74); die letzte Fassung war klar (0,99)). Jeder geschnittene Sprechteil
        # wird deshalb so abgehört, wie ihn ein Zuschauer hört: ohne Zusammenhang, ohne Vorgabe. Unter 0,85 → Warnung.
        if not P.get("klarheit", True):
            return
        try:
            import mlx_whisper
            _w = f"{CL}/talk{k}.wav"
            run(["ffmpeg", "-v", "error", "-y", "-i", f"{CL}/talk{k}.mp4", "-vn", "-ac", "1", "-ar", "16000", "-af", "dynaudnorm", _w])
            _r = mlx_whisper.transcribe(_w, path_or_hf_repo=os.environ.get("VELOX_WHISPER", "mlx-community/whisper-large-v3-turbo"), word_timestamps=True,
                                        condition_on_previous_text=False, temperature=0.0)
            os.remove(_w)
            _ww = [x for sg in _r["segments"] for x in sg.get("words", [])]
            _pr = sum(float(x.get("probability", 0)) for x in _ww) / max(1, len(_ww))
            TALK[k]["klar"] = round(_pr, 2)
            print(f"   Verständlichkeit {_pr:.2f}" + ("  ⚠ UNDEUTLICH – andere Fassung prüfen" if _pr < 0.85 else "") + f" – gehört: „{_r['text'].strip()[:90]}“", flush=True)
        except Exception as _e:  # noqa: BLE001
            print(f"   Verständlichkeit nicht prüfbar ({_e})", flush=True)

    def ff_teil(k):
        """Sprechteil k mit ffmpeg schneiden (ohne Resolve oder wenn Resolve ausfällt)."""
        s = TALK[k]
        a, d, z = s["a"], s["d"], s["_z"]
        stab = stabil(s["clip"], a, d, f"talk{k}")
        vf = f"fps={FPS},scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920," + \
             (f"scale=iw*{z}:ih*{z}:flags=lanczos,crop=1080:1920:(iw-1080)/2:(ih-1920)*0.38," if z > 1 else "") + s["_fahrt"] + f"{farbe(s['clip'])},setsar=1,format=yuv420p"
        run(["ffmpeg", "-v", "error", "-y", *(["-i", stab] if stab else []), "-ss", f"{a:.3f}", "-t", f"{d:.3f}", "-i", src(s["clip"]),
             *(["-map", "0:v", "-map", "1:a"] if stab else []), "-vf", vf,
             "-af", f"afade=t=in:st=0:d=0.03,afade=t=out:st={max(0.0, d - 0.03):.3f}:d=0.03,aresample=48000", "-ac", "2",
             "-c:v", "libx264", "-preset", "fast", "-crf", "14", "-c:a", "aac", "-b:a", "256k", f"{CL}/talk{k}.mp4"])
        print(f"Sprechteil {k}: {s['clip']} {a:.2f}–{a + d:.2f} s  „{' '.join(w['t'] for w in s['ws'])[:70]}“", flush=True)
        klar(k)

    # Sprechteile: Kanten in die Ruhe neben das erste/letzte Wort legen
    TALK, OFF, t = [], [], 0.0
    for k, s in enumerate(P["sprechteile"]):
        ws = [w for w in W(s["clip"]) if s["von"] - 0.06 <= w["s"] and w["e"] <= s["bis"] + 0.06]
        if not ws:
            sys.exit(f"Sprechteil {k}: im Bereich {s['von']}–{s['bis']} von {s['clip']} steht kein Wort")
        alle = W(s["clip"])
        i0, i1 = alle.index(ws[0]), alle.index(ws[-1])
        vor = alle[i0 - 1]["e"] if i0 > 0 else 0.0
        nach = alle[i1 + 1]["s"] if i1 + 1 < len(alle) else dauer(src(s["clip"]))
        _ein = einsatz(src(s["clip"]), ws[0])
        if _ein > ws[0]["s"] + 0.05:
            print(f"Sprechteil {k}: „{ws[0]['t']}“ beginnt erst bei {_ein:.2f} s statt {ws[0]['s']:.2f} s – davor spricht jemand anderes/leise → abgeschnitten", flush=True)
            vor = max(vor, _ein - 0.2)
        a = kante(src(s["clip"]), _ein, True, vor)
        b = kante(src(s["clip"]), ws[-1]["e"], False, nach)
        d = round((b - a) * FPS) / FPS
        TALK.append({**s, "a": a, "d": d, "ws": ws})
        OFF.append(t)
        t += d
        # Bildwechsel am Schnitt: Folgt ein Teil aus DERSELBEN Aufnahme, springt das Bild sonst sichtbar („Jump Cut“). Ohne Vorgabe
        # im Plan wechselt der Ausschnitt deshalb ab (ganz / etwas näher) – je Stufe verschieden stark.
        # ZEIGT (Inhaber 17.10.): Wird etwas gezeigt (Plan "zeigt": true oder Zeige-Wörter), gibt es dort keinen Zoom und keine Kamerafahrt –
        # sonst fallen die Produkte am Bildrand heraus.
        ZW = {"hier", "dieser", "diese", "dieses", "diesen", "schaut", "seht", "guckt", "zeige", "zeigen", "sowas", "solche"}
        TALK[-1]["_zeigt"] = bool(s.get("zeigt")) or (s.get("zeigt") is None and any(norm(w["t"]) in ZW for w in ws))
        if TALK[-1]["_zeigt"]:
            z = 1.0
        elif "zoom" in s:
            z = float(s["zoom"])
        else:
            z = PUNCH if k > 0 and TALK[-2]["clip"] == s["clip"] and TALK[-2].get("_z", 1.0) == 1.0 else 1.0
        TALK[-1]["_z"] = z
        fahrt = ""
        if (STUFE == "premium" or (STUFE == "viral" and k == 0)) and d >= 1.5 and s.get("fahrt", True) and not TALK[-1]["_zeigt"]:
            n_, r_ = max(1, int(d * FPS)), min(0.06, 0.012 * d)          # langsames Heranfahren, höchstens 6 %
            zf = f"(1+{r_:.4f}*on/{n_})"
            fahrt = (f"perspective=x0='W*0.5*(1-1/{zf})':y0='H*0.38*(1-1/{zf})':x1='W-W*0.5*(1-1/{zf})':y1='H*0.38*(1-1/{zf})':"
                     f"x2='W*0.5*(1-1/{zf})':y2='H-H*0.62*(1-1/{zf})':x3='W-W*0.5*(1-1/{zf})':y3='H-H*0.62*(1-1/{zf})':sense=source:interpolation=cubic:eval=frame,")
        TALK[-1]["_fahrt"] = fahrt
        if not RESCUT:
            ff_teil(k)
    if RESCUT:
        try:
            res_projekt()
            _cdl = {}
            teile_r = []
            for k, s in enumerate(TALK):
                if s["clip"] not in _cdl:
                    _cdl[s["clip"]] = RES.auto_cdl(src(s["clip"]), STUFE)
                zt = zittern_messen(src(s["clip"]), s["a"], s["d"]) if P.get("stabil", True) else 0.0
                teile_r.append({"src": src(s["clip"]), "a": s["a"], "d": s["d"], "zoom": s["_z"], "stabil": zt > 0.3, "cdl": _cdl[s["clip"]]})
                print(f"Sprechteil {k}: {s['clip']} {s['a']:.2f}–{s['a'] + s['d']:.2f} s → Resolve" + (f" (stabilisiert, Zittern {zt:.2f})" if zt > 0.3 else "") +
                      f"  „{' '.join(w['t'] for w in s['ws'])[:60]}“", flush=True)
            zeiten, tl_name = RES.schnitt(teile_r, f"{CL}/resolve_schnitt.mp4",
                                          f"Velox {P.get('titel') or os.path.basename(os.path.dirname(ARB.rstrip('/')))} [{STUFE}] {time.strftime('%d.%m. %H-%M')}")
            print(f"DaVinci Resolve: {len(zeiten)} Teile aus den Original-Clips geschnitten, farbkorrigiert, gerendert – Timeline „{tl_name}“", flush=True)
            OFF = []
            for k, (z0, z1) in enumerate(zeiten):   # fertigen Resolve-Schnitt in die Sprechteile zerlegen (für Gesicht, Untertitel, Prüfung)
                d_ = round((z1 - z0) * FPS) / FPS
                TALK[k]["d"] = d_
                OFF.append(z0)
                vf = f"fps={FPS},scale=1080:1920:flags=lanczos,setsar=1," + TALK[k]["_fahrt"] + ("vignette=PI/6," if STUFE == "premium" else "") + "format=yuv420p"
                run(["ffmpeg", "-v", "error", "-y", "-ss", f"{z0:.3f}", "-t", f"{d_:.3f}", "-i", f"{CL}/resolve_schnitt.mp4", "-vf", vf,
                     "-af", f"afade=t=in:st=0:d=0.03,afade=t=out:st={max(0.0, d_ - 0.03):.3f}:d=0.03,aresample=48000", "-ac", "2",
                     "-c:v", "libx264", "-preset", "fast", "-crf", "14", "-c:a", "aac", "-b:a", "256k", f"{CL}/talk{k}.mp4"])
                klar(k)
            t = OFF[-1] + TALK[-1]["d"]
        except Exception as e:  # noqa: BLE001
            print(f"DaVinci Resolve-Schnitt fehlgeschlagen ({e}) – schneide mit ffmpeg", flush=True)
            RES = None
            RESCUT = False
            OFF, t = [], 0.0
            for k in range(len(TALK)):
                OFF.append(t)
                ff_teil(k)
                t += TALK[k]["d"]
    T_TALK = t
    # GESICHT (erster Test über die Webseiten-Strecke, 13.10.: nahes Selfie – Untertitel und Karten lagen quer über Augen und Mund).
    # Je Sprechteil wird gemessen, wo das Gesicht im fertigen Bild liegt; Untertitel und Grafiken bekommen ihren Platz daneben:
    # unter dem Kinn, sonst über dem Kopf, und wenn das Gesicht alles füllt, ganz unten (Hals statt Augen). Grafiken, die dann
    # keinen freien Platz haben, entfallen.
    import cv2
    _kas = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    UT_Y, FREI_AB, GESICHT, GESICHT_X = [], [], [], []
    for k, s in enumerate(TALK):
        cap = cv2.VideoCapture(f"{CL}/talk{k}.mp4")
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        oben, unten, links, rechts = [], [], [], []
        for q in (0.08, 0.2, 0.32, 0.44, 0.56, 0.68, 0.8, 0.92):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * q))
            ok, fr = cap.read()
            if not ok:
                continue
            g = cv2.cvtColor(cv2.resize(fr, (540, 960)), cv2.COLOR_BGR2GRAY)
            fs = _kas.detectMultiScale(cv2.equalizeHist(g), 1.1, 7, minSize=(60, 60))
            if len(fs):
                x, y, w, h = max(fs, key=lambda b: b[2] * b[3])
                oben.append((y - 0.15 * h) * 2)       # mit Haaransatz
                unten.append((y + 1.08 * h) * 2)      # bis unters Kinn
                links.append(x * 2)
                rechts.append((x + w) * 2)
        cap.release()
        # Kontrolle 13.10. (Cheesecake): Ein einzelner Fehltreffer (Logo auf der Schürze) galt als „Gesicht“ und schob die Untertitel
        # über den Kopf. Deshalb: mindestens drei Treffer, und es zählt die Mitte der Treffer (Median) plus etwas Spielraum –
        # ein Ausreißer verschiebt nichts mehr.
        if len(oben) >= 3:
            o_, u_ = sorted(oben), sorted(unten)
            ft, fb = o_[len(o_) // 2] - 40, u_[len(u_) // 2] + 40
        else:
            ft, fb = None, None
        GESICHT.append((ft, fb))
        GESICHT_X.append((min(links) - 30, max(rechts) + 30) if len(oben) >= 3 else None)
        y = 1090
        if ft is not None and ft - 70 < y < fb + 70:
            y = fb + 80 if fb + 80 <= 1330 else (ft - 90 if ft - 90 >= 300 else 1340)
        UT_Y.append(int(y))
        FREI_AB.append(int(max(1250, y + 120)) if y < 1250 else None)   # ab hier dürfen Grafiken unter den Untertiteln stehen
        if ft is not None:
            print(f"Sprechteil {k}: Gesicht {ft:.0f}–{fb:.0f} px → Untertitel bei {y:.0f}", flush=True)
    for k in range(1, len(UT_Y)):   # Untertitel springen nicht um ein paar Pixel von Schnitt zu Schnitt: nahe Höhen angleichen
        if abs(UT_Y[k] - UT_Y[k - 1]) < 90 and (GESICHT[k][0] is None or not (GESICHT[k][0] - 70 < UT_Y[k - 1] < GESICHT[k][1] + 70)):
            UT_Y[k] = UT_Y[k - 1]
            FREI_AB[k] = FREI_AB[k - 1] if FREI_AB[k] is not None else None
    out_t = lambda teil, st: OFF[teil] + (st - TALK[teil]["a"])
    teil_ende = lambda teil: OFF[teil] + TALK[teil]["d"]

    def broll(name, c, start, dur, speed=0.5):
        st_ = stabil(c, start, dur * speed + 0.25, name, FPS / speed)
        vf = f"setpts={1 / speed}*PTS,fps={FPS},scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,{farbe(c)},setsar=1,format=yuv420p"
        run(["ffmpeg", "-v", "error", "-y", *(["-i", st_] if st_ else ["-ss", f"{start:.3f}", "-t", f"{dur * speed + 0.25:.3f}", "-i", src(c)]), "-an", "-vf", vf, "-t", f"{dur:.3f}",
             "-c:v", "libx264", "-preset", "fast", "-crf", "14", f"{CL}/{name}.mp4"])
        return f"{CL}/{name}.mp4"

    EIN, EIN_NAMEN = [], []
    for k, e in enumerate(P.get("einschuebe") or []):
        a = max(OFF[e["teil"]], out_t(e["teil"], e["ab"]) - 0.05)
        b = min(teil_ende(e["teil"]), out_t(e["teil"], e["bis"]) - 0.02) if e.get("bis") else teil_ende(e["teil"])
        # Inhaber 17.10.: „das Bild von den Cheesecakes am Anfang ist zu kurz, da sieht man kaum was – wirkt abgehackt“ → mindestens 2 s
        # (läuft dann über den nächsten Satz weiter), höchstens bis kurz vor Schluss
        if b - a < 2.0:
            b = min(a + 2.0, (OFF[-1] + TALK[-1]["d"]) - 0.3)
        if b - a >= 0.5:
            EIN.append((broll(f"ein{k}", e["clip"], e.get("start", 0.0), b - a, e.get("tempo", 0.6)), a, b - a))
            EIN_NAMEN.append(e["clip"])

    # Musik und Schluss-Montage
    MUS = (P["musik"] if os.path.isabs(P["musik"]) else os.path.join(MASCH, "musik", P["musik"])) if P.get("musik") else None   # Name aus musik/ oder ganzer Pfad (material/MUSIK/…)
    if MUS and not os.path.exists(MUS):
        print(f"Musik {MUS} nicht gefunden – ohne Musik", flush=True)
        MUS = None
    mont_files, T_ENDE, BEAT, PHASE = [], None, 0.5, 0.0
    M = P.get("montage") if MUS else None
    if MUS:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", MUS, "-t", "60", "-ac", "1", "-ar", "11025", "-f", "s16le", "-"], capture_output=True).stdout
        x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
        hop = 256
        env = np.array([np.sqrt((x[i:i + 1024] ** 2).mean()) for i in range(0, len(x) - 1024, hop)])
        on = np.maximum(0, np.diff(env))
        on -= on.mean()
        ac = np.correlate(on, on, "full")[len(on) - 1:]
        lag = lambda bpm: 60.0 / bpm * 11025 / hop
        best = max(range(int(lag(170)), int(lag(80))), key=lambda L: ac[L])
        BEAT = best * hop / 11025
        PHASE = max(range(best), key=lambda p: on[p::best][:40].sum()) * hop / 11025
    if M:
        nb = lambda sek: max(1, round(sek / BEAT))
        SCH = BEAT * nb(M.get("schnitt_s", 0.75))
        ENDE_D = BEAT * nb(M.get("ende_s", 2.9))
        mont_files = [broll(f"mont{k}", c, s, SCH) for k, (c, s) in enumerate(M["clips"])] + [broll("mont_ende", M["ende"][0], M["ende"][1], ENDE_D)]
        TOTAL = T_TALK + SCH * len(M["clips"]) + ENDE_D
        T_ENDE = TOTAL - ENDE_D
    else:
        TOTAL = T_TALK

    GRADED = None
    if RESCUT:      # Sprechteile sind schon in Resolve geschnitten und gegradet – nur Montage und Zwischenbilder noch durch Resolve
        for i, (f_, st, du) in enumerate(EIN):
            try:
                g_ = f_[:-4] + "_resolve.mp4"
                RES.grade(f_, g_, STUFE, behalten=False)
                EIN[i] = (g_, st, du)
            except Exception:  # noqa: BLE001
                run(["ffmpeg", "-v", "error", "-y", "-i", f_, "-vf", farbe_ff(EIN_NAMEN[i]) + ",format=yuv420p", "-c:v", "libx264", "-preset", "fast", "-crf", "14", f_[:-4] + "_ff.mp4"])
                EIN[i] = (f_[:-4] + "_ff.mp4", st, du)
        for i, (f_, name) in enumerate(zip(mont_files, ([c for c, _ in M["clips"]] + [M["ende"][0]]) if M else [])):
            try:
                RES.grade(f_, f_[:-4] + "_resolve.mp4", STUFE, behalten=False)
                os.replace(f_[:-4] + "_resolve.mp4", f_)
            except Exception:  # noqa: BLE001
                run(["ffmpeg", "-v", "error", "-y", "-i", f_, "-vf", farbe_ff(name) + ",format=yuv420p", "-c:v", "libx264", "-preset", "fast", "-crf", "14", f_[:-4] + "_ff.mp4"])
                os.replace(f_[:-4] + "_ff.mp4", f_)
        RES.speichern()
    elif RES:
        teile = [(f"{CL}/talk{k}.mp4", s["clip"]) for k, s in enumerate(TALK)] + \
                list(zip(mont_files, ([c for c, _ in M["clips"]] + [M["ende"][0]]) if M else []))
        roh = f"{CL}/schnitt_roh.mp4"
        open(f"{CL}/schnitt_roh.txt", "w").write("".join(f"file '{p_}'\n" for p_, _ in teile))
        run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", f"{CL}/schnitt_roh.txt", "-an", "-c", "copy", roh])
        shots, f0 = [], 0
        for p_, name in teile:
            n_ = int(round(dauer(p_) * FPS))
            shots.append((f0, f0 + n_, name))
            f0 += n_
        try:
            res_projekt()
            RES.grade(roh, f"{CL}/schnitt_resolve.mp4", STUFE, behalten=True, shots=shots,
                      name=f"Velox {P.get('titel') or os.path.basename(os.path.dirname(ARB.rstrip('/')))} [{STUFE}] {time.strftime('%d.%m. %H-%M')}")
            for i, (f_, st, du) in enumerate(EIN):
                g_ = f_[:-4] + "_resolve.mp4"
                RES.grade(f_, g_, STUFE, behalten=False)
                EIN[i] = (g_, st, du)
            GRADED = f"{CL}/schnitt_resolve.mp4"
            RES.speichern()
            print(f"DaVinci Resolve: Schnitt ({len(shots)} Teile, {len(set(n for _, _, n in shots))} Aufnahmen) farbkorrigiert und gerendert; "
                  f"Timeline bleibt im Resolve-Projekt", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"DaVinci Resolve fehlgeschlagen ({e}) – Farbe mit ffmpeg", flush=True)
            for p_, name in teile + list(zip([f_ for f_, _, _ in EIN], EIN_NAMEN)):
                run(["ffmpeg", "-v", "error", "-y", "-i", p_, "-vf", farbe_ff(name) + ",format=yuv420p", "-c:v", "libx264", "-preset", "fast", "-crf", "14",
                     "-c:a", "copy", p_[:-4] + "_ff.mp4"])
                os.replace(p_[:-4] + "_ff.mp4", p_)

    # PREMIUM MIT DEM MOTION-GRAPHICS-BAUKASTEN (Inhaber 17.10.: „für Premium viel zu schlecht – so wie das Yacht-Video auf der
    # Webseite muss jedes Premium-Video aussehen, mit richtig krassen Motion Graphics“). Steht im Plan "premium": {…}, zeichnet der
    # Remotion-Baukasten (worker/premium, derselbe wie bei den Novus-Videos) das ganze Video: Kamerafahrten, Vollbild-Karten, Text
    # hinter der Person, Zähler, Abläufe, Listen, Chat/Suche/Anruf, Zwischenbilder als Vollbild/Karte/Handy, Untertitel, Töne, Abspann.
    # CLAUDE wählt die Bausteine (früher Gemini). Zeiten im Plan wie überall: {"teil": k, "zeit": Sekunde im Rohclip}.
    if STUFE == "premium" and P.get("premium"):
        try:
            import premium2
            # Wörter des fertigen Schnitts (mit den Schreibweisen des Plans)
            W_OUT, W_TEIL = [], []
            for k, s in enumerate(TALK):
                ers = s.get("ersetzen") or {}
                for w in s["ws"]:
                    tx = ers.get(w["t"], ers.get(w["t"].strip(".,!?"), None))
                    if tx is not None and w["t"] not in ers:
                        tx = tx + (re.search(r"[.,!?]+$", w["t"]) or re.match("", "")).group(0)
                    tx = w["t"] if tx is None else tx
                    if not tx or tx in ("…", "..."):
                        continue
                    W_OUT.append((tx, round(out_t(k, w["s"]), 3), round(out_t(k, w["e"]), 3)))
                    W_TEIL.append(k)

            def wi(z, ende=False):
                """{"teil", "zeit"} → Nummer des Wortes im fertigen Video"""
                if isinstance(z, int):
                    return z
                ot = out_t(int(z["teil"]), float(z["zeit"]))
                if ende:
                    c = [i for i, w in enumerate(W_OUT) if w[1] <= ot + 0.05]
                    return c[-1] if c else 0
                c = [i for i, w in enumerate(W_OUT) if w[2] > ot - 0.02]
                return c[0] if c else len(W_OUT) - 1
            PR = json.loads(json.dumps(P["premium"]))
            if PR.get("hook"):
                PR["hook"]["bis"] = wi(PR["hook"].get("bis", {"teil": 0, "zeit": TALK[0]["a"] + 2.5}), True)
            for g in PR.get("grafiken") or []:
                g["von"], g["bis"] = wi(g["von"]), wi(g["bis"], True)
                for it in g.get("texte") or []:
                    if isinstance(it.get("wort"), dict):
                        it["wort"] = wi(it["wort"])
            premium2._zahlen(PR)        # „3“ → 3 (sonst verwirft der Baukasten Zähler und Nummern)
            # NICHTS VERDECKEN, WAS GEZEIGT WIRD (Inhaber 17.10.: „bei der Aufzählung sieht man ihn gar nicht“ – Sprecher-Karte über
            # den Produkten in seinen Händen; „bei ‚drei dieser Special Cheesecakes‘ sieht man den Kuchen nicht“ – Vollbild-Zahl darüber).
            # Ein Sprechteil „zeigt“, wenn der Plan es sagt ("zeigt": true) oder Zeige-Wörter fallen (hier, dieser, schaut …).
            # Dort gibt es keine Vollbild-Karten und keine Sprecher-Karte – die Grafik wird zur kleinen Bühnen-Grafik über dem Kopf.
            ZEIGT = [bool(s.get("_zeigt")) for s in TALK]
            print("Sprechteile, in denen etwas gezeigt wird: " + (", ".join(str(k) for k, z in enumerate(ZEIGT) if z) or "keine"), flush=True)

            def zeigt_bei(i0, i1):
                return any(ZEIGT[W_TEIL[i]] for i in range(max(0, i0), min(len(W_TEIL), i1 + 1)))
            for g in PR.get("grafiken") or []:
                if not zeigt_bei(g["von"], g["bis"]):
                    continue
                if g.get("karte"):
                    g["karte"] = False
                    print(f"   {g['art']}: ohne Sprecher-Karte – dort wird etwas gezeigt", flush=True)
                if g.get("art") == "voll_zahl":
                    g["art"] = "zahl"
                    g["gross"] = g.get("gross") or g.get("kicker") or ""
                    print("   voll_zahl → Zähler über dem Kopf – dort wird etwas gezeigt", flush=True)
                elif g.get("art") == "voll_wort":
                    g["art"] = "kasten" if len(str(g.get("gross") or "")) <= 18 else "wort"
                    print(f"   voll_wort → {g['art']} über dem Kopf – dort wird etwas gezeigt", flush=True)
            bet = {norm(x) for x in PR.get("betont") or [] if isinstance(x, str)}
            PR["betont"] = [i for i, w in enumerate(W_OUT) if norm(w[0]) in bet][:24]
            # KOPF ÜBERALL GLEICH HOCH (Test 17.10.: Der Baukasten legt das Bild einmal fürs ganze Video – bei näher gefilmten
            # Teilen lagen die Untertitel auf der Stirn). Jeder Sprechteil wird so vergrößert/verschoben, dass der Haaransatz bei
            # ~715 px liegt (darüber ist Platz für Untertitel und Grafik); verkleinert wird nie, höchstens 1,5-fach vergrößert.
            import cv2 as _cv
            _cc = _cv.CascadeClassifier(_cv.data.haarcascades + "haarcascade_frontalface_default.xml")
            for k in range(len(TALK)):
                cap = _cv.VideoCapture(f"{CL}/talk{k}.mp4")
                n_ = int(cap.get(_cv.CAP_PROP_FRAME_COUNT)) or 1
                fb = []
                for q in (0.15, 0.35, 0.55, 0.75, 0.9):
                    cap.set(_cv.CAP_PROP_POS_FRAMES, int(n_ * q))
                    ok, im = cap.read()
                    if ok:
                        fs = _cc.detectMultiScale(_cv.equalizeHist(_cv.cvtColor(im, _cv.COLOR_BGR2GRAY)), 1.1, 6, minSize=(160, 160))
                        if len(fs):
                            fb.append(max(fs, key=lambda b: b[2] * b[3]))
                cap.release()
                if len(fb) < 2:
                    continue
                x_, y_, w_, h_ = np.median(np.array(fb), axis=0)
                hair = y_ - 0.2 * h_
                ziel_h = 715.0
                if ZEIGT[k]:
                    # ZEIGT ER ETWAS, WIRD NIE HINEINGEZOOMT (Test 17.10. „Unter 10 €“: ×1,5 schnitt alle Kuchen auf der Theke weg). Stattdessen
                    # wird das Bild verkleinert und nach unten gesetzt – alles bleibt sichtbar, der Kopf rutscht unter die Grafiken; der freie
                    # Platz darüber und daneben ist eine unscharfe, abgedunkelte Kopie desselben Bildes.
                    if hair >= ziel_h - 60:
                        continue
                    s_ = max(0.68, min(1.0, (1920.0 - ziel_h) / (1920.0 - hair)))
                    W2, H2 = int(1080 * s_) // 2 * 2, int(1920 * s_) // 2 * 2
                    run(["ffmpeg", "-v", "error", "-y", "-i", f"{CL}/talk{k}.mp4", "-filter_complex",
                         f"[0:v]split[a][b];[a]scale=1080:1920,boxblur=28:4,eq=brightness={-0.12 if (P.get('premium') or {}).get('ton') == 'ernst' else 0.02}:saturation=1.1[bg];[b]scale={W2}:{H2}:flags=lanczos[fg];"
                         f"[bg][fg]overlay=(1080-w)/2:1920-h,setsar=1[v]", "-map", "[v]", "-map", "0:a",
                         "-c:v", "libx264", "-preset", "fast", "-crf", "14", "-c:a", "copy", f"{CL}/talk{k}_lage.mp4"])
                    os.replace(f"{CL}/talk{k}_lage.mp4", f"{CL}/talk{k}.mp4")
                    print(f"Sprechteil {k}: zeigt etwas → Bild verkleinert (×{s_:.2f}), nichts abgeschnitten, Haaransatz {hair:.0f} → {1920 - H2 + hair * s_:.0f} px", flush=True)
                    continue
                sc = max(1.0, min(1.5, 470.0 / w_ if w_ < 470 else 1.0))
                # nach dem Vergrößern um die Gesichtsmitte: wohin muss das Bild, damit der Haaransatz auf ziel_h liegt?
                cx_, cy_ = x_ + w_ / 2, y_ + h_ / 2
                hair_s = cy_ + (hair - cy_) * sc
                dy = ziel_h - hair_s
                need = abs(dy) * 2 / 1920 + 1.0          # so viel Vergrößerung braucht die Verschiebung ohne schwarzen Rand
                if need > sc:
                    sc = min(1.5, need)
                    hair_s = cy_ + (hair - cy_) * sc
                    dy = ziel_h - hair_s
                W2, H2 = int(1080 * sc) // 2 * 2, int(1920 * sc) // 2 * 2
                ox = min(max(0, int(cx_ * sc - 540)), W2 - 1080)
                oy = min(max(0, int(hair * sc - ziel_h)), H2 - 1920)
                if sc <= 1.01 and abs(hair - ziel_h) < 60:
                    continue
                run(["ffmpeg", "-v", "error", "-y", "-i", f"{CL}/talk{k}.mp4", "-vf", f"scale={W2}:{H2}:flags=lanczos,crop=1080:1920:{ox}:{oy},setsar=1",
                     "-c:v", "libx264", "-preset", "fast", "-crf", "14", "-c:a", "copy", f"{CL}/talk{k}_lage.mp4"])
                os.replace(f"{CL}/talk{k}_lage.mp4", f"{CL}/talk{k}.mp4")
                print(f"Sprechteil {k}: Kopf ausgerichtet (×{sc:.2f}, Haaransatz {hair:.0f} → {hair * sc - oy:.0f} px)", flush=True)
            # Rohschnitt ohne Schrift: gegradete Sprechteile hintereinander (Ton wie im fertigen Video)
            cut = f"{CL}/premium_roh.mp4"
            ins = []
            for k in range(len(TALK)):
                ins += ["-i", f"{CL}/talk{k}.mp4"]
            if False:
                ins = ["-i", GRADED] + ins
                fcx = "".join(f"[{k + 1}:a]" for k in range(len(TALK))) + f"concat=n={len(TALK)}:v=0:a=1[a]"
                run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", fcx, "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-crf", "14", "-preset", "fast", "-c:a", "aac", "-b:a", "256k", cut])
            else:
                fcx = "".join(f"[{k}:v][{k}:a]" for k in range(len(TALK))) + f"concat=n={len(TALK)}:v=1:a=1[v][a]"
                run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", fcx, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "14", "-preset", "fast", "-c:a", "aac", "-b:a", "256k", cut])
            _logo = P.get("logo")
            if _logo and not os.path.isabs(_logo):
                _logo = os.path.join(ORD, _logo)
            r_ = premium2.render(cut, W_OUT, P.get("titel") or "", ZIEL, None, cut_times=OFF[1:], logo=_logo if _logo and os.path.exists(_logo) else None,
                                 accent=AKZ, seed_text=P.get("titel") or ARB, plan=PR, brolls=[{"file": f_, "t": st, "dur": du, **({"art": "broll"} if ZEIGT[max(i for i in range(len(OFF)) if OFF[i] <= st + 0.01)] else {})} for f_, st, du in EIN],
                                 outro=P.get("abspann", True), log=lambda m: print(m, flush=True),
                                 lage=dict(s=1.0, tx=0, ty=0, ox=540, oy=1150))   # Lage hat claude_schnitt je Teil gesetzt – nicht noch einmal fürs ganze Video
            if r_:
                json.dump({"premium": True, "sprechteile": [(s["clip"], round(s["a"], 3), round(s["a"] + s["d"], 3)) for s in TALK], "elemente": r_[1].get("elemente")},
                          open(os.path.join(ARB, "schnittliste.json"), "w"), ensure_ascii=False, indent=1)
                print(f"fertig [premium · Motion-Graphics-Baukasten]: {ZIEL}  ({dauer(ZIEL):.1f} s)", flush=True)
                return
            print("Premium-Baukasten lieferte nichts – baue mit den einfachen Grafiken", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"Premium-Baukasten fehlgeschlagen ({e}) – baue mit den einfachen Grafiken", flush=True)

    # Grafiken: jede bekommt ihren Platz – unten (unter den Untertiteln) nebeneinander, „pille“ oben; nichts überlappt
    Y_UT, Y_UNTEN, Y_OBEN, SAFE_B = 1090, 1250, 250, 1400
    G, belegt, STUMM = [], [], set()
    frei = lambda x0, y0, x1, y1, a, b: not any(x0 < bx1 + 14 and x1 > bx0 - 14 and y0 < by1 + 14 and y1 > by0 - 14 and a < bb and b > ba for bx0, by0, bx1, by1, ba, bb in belegt)
    liste_n = 0
    for k, g in enumerate(P.get("grafiken") or []):
        if STUFE == "clean" and g["art"] != "ort":     # clean: keine Karten und Emojis – nur der Ort am Schluss darf stehen
            continue
        stufen = [(g, g.get("ab"), g.get("bis"))]
        if g["art"] == "liste":   # wächst Punkt für Punkt: je Punkt eine Stufe, jede löst die vorige ab
            pk = g["punkte"]
            stufen = [({**g, "n": i}, (g.get("ab") if i == 0 else pk[i - 1][1]), (pk[i][1] if i < len(pk) else g.get("bis"))) for i in range(len(pk) + 1)]
            stufen = [s_ for s_ in stufen if s_[1] is not None]
        # PREMIUM – Zahl zählt hoch („99 %“, „24 Stunden“): Beginnt die große Zeile einer Preis-Karte mit einer ganzen Zahl, läuft sie
        # in einer halben Sekunde von unten auf ihren Wert (gleiche Stellenzahl, damit die Karte nicht springt).
        zaehl = False
        _zm = re.match(r"(\d{2,5})(\D.*)?$", g.get("gross", "")) if g["art"] == "preis" else None
        if STUFE == "premium" and _zm and g.get("ab") is not None and g.get("zaehlen", True):
            ziel_, rest_ = int(_zm.group(1)), _zm.group(2) or ""
            unten_ = 10 ** (len(_zm.group(1)) - 1)
            werte = [int(unten_ + (ziel_ - unten_) * (1 - (1 - i / 6) ** 2)) for i in range(6)]
            zeiten = [g["ab"], g["ab"] + 0.20] + [g["ab"] + 0.20 + 0.06 * i for i in range(1, 6)]
            stufen = [({**g, "gross": f"{werte[i]}{rest_}"}, zeiten[i], zeiten[i + 1]) for i in range(6)] + [(g, zeiten[6], g.get("bis"))]
            zaehl = True
        for si, (gs, ab, bis) in enumerate(stufen):
            a = out_t(g["teil"], ab)
            b = min(teil_ende(g["teil"]) - 0.05, out_t(g["teil"], bis) - (0.0 if g["art"] == "liste" or zaehl else 0.05)) if bis else teil_ende(g["teil"]) - 0.05
            if b - a < (0.03 if zaehl else 0.4):
                continue
            pfad = f"{GR}/g{k}_{si}.png"
            (w, h), rand = grafik(gs, AKZ, pfad)
            _gf = GESICHT[g["teil"]]
            _auf_gesicht = lambda ya, yb: _gf[0] is not None and ya < _gf[1] and yb > _gf[0]
            _yu = FREI_AB[g["teil"]]
            if g["art"] == "liste":
                if _yu is None or _yu - 40 + h > SAFE_B + 20 or _auf_gesicht(_yu - 40, _yu - 40 + h):
                    if si == 0:
                        print("Liste weggelassen – kein freier Platz neben Gesicht und Untertiteln", flush=True)
                    continue
                x0, y0 = 90, _yu - 40
                if si == 0:
                    belegt.append((x0, y0, x0 + w, y0 + h, a, teil_ende(g["teil"])))
                G.append((pfad, a, b, x0 + w / 2, y0, rand, si > 0))
                continue
            if g["art"] == "pfeil":      # Spitze des Fingers auf den Punkt (x, y) des fertigen Bildes; nie auf Gesicht oder Untertitel
                # Erst von oben (Schild über dem Punkt); liegt das auf dem Gesicht (Produkt wird unters Kinn gehalten – Cheesecake-
                # Test 13.10.), dann von unten. Geht beides nicht, entfällt der Pfeil.
                _gx = GESICHT_X[g["teil"]]
                x0, gesetzt = min(1010 - w, max(70, g["x"] - w / 2)), False
                for von_unten in (False, True):
                    y0 = g["y"] + 6 if von_unten else g["y"] - h
                    if y0 < 180 or y0 + h > SAFE_B or (_auf_gesicht(y0, y0 + h) and (_gx is None or (x0 < _gx[1] and x0 + w > _gx[0]))) or \
                            (y0 < UT_Y[g["teil"]] + 70 and y0 + h > UT_Y[g["teil"]] - 70) or not frei(x0, y0, x0 + w, y0 + h, a, b):
                        continue
                    if von_unten:
                        grafik({**gs, "_von_unten": True}, AKZ, pfad)
                    belegt.append((x0, y0, x0 + w, y0 + h, a, b))
                    G.append((pfad, a, b, x0 + w / 2, y0, rand, False))
                    gesetzt = True
                    break
                if not gesetzt:
                    print(f"Pfeil „{g['text']}“ weggelassen – läge auf Gesicht, Untertitel oder einer anderen Grafik", flush=True)
                continue
            # zeigt die Person gerade etwas (Produkte unten im Bild), gehören Karten nach oben – nie auf die Produkte
            y0 = Y_OBEN if g["art"] == "pille" or TALK[g["teil"]].get("_zeigt") else (_yu if _yu is not None else 99999) + (0 if g["art"] in ("preis", "ort") else 40)
            if y0 + h > SAFE_B + 20 or _auf_gesicht(y0, y0 + h):   # unten kein Platz → oben versuchen, sonst weglassen
                y0 = Y_OBEN if not _auf_gesicht(Y_OBEN, Y_OBEN + h) and abs(UT_Y[g["teil"]] - Y_OBEN) > h + 60 else 99999
            platz = None
            for xm in (540, 400, 680, 300, 780, 220):     # Mitte zuerst, dann links/rechts daneben
                x0 = xm - w / 2
                if x0 >= 70 and x0 + w <= (870 if y0 + h > 770 else 1010) and y0 + h <= SAFE_B + 20 and frei(x0, y0, x0 + w, y0 + h, a, b):
                    platz = xm
                    break
            if platz is None:
                print(f"Grafik „{g.get('text') or g.get('zeichen') or g['art']}“ weggelassen – kein freier Platz", flush=True)
                continue
            belegt.append((platz - w / 2, y0, platz + w / 2, y0 + h, a, b))
            G.append((pfad, a, b, platz, y0, rand, zaehl and si > 0))
            if zaehl and si > 0:
                STUMM.add(pfad)
    # zwei gleichzeitige Marken mittig als Paar ausrichten (statt eine mittig, eine daneben)
    if M and M.get("schild"):
        pfad = f"{GR}/schild.png"
        grafik({"art": "schild", "zeile1": M["schild"][0], "zeile2": M["schild"][1] if len(M["schild"]) > 1 else ""}, AKZ, pfad)
        G.append((pfad, T_ENDE + 0.25, TOTAL, 540, 1010, 0, False))

    # UNTERTITEL-STILE (Launch 16.10.: „bei jedem Kunden andere Untertitel – je nach Branche und Stimmung“). Dieselben 11 Stile
    # wie die Vorschau im Studio, dazu „akzent“ (Hausstil: kleine weiße Zeile, Schlagwort groß und kursiv). "untertitel":
    # {"stil": …, "farbe": "#hex"} im Plan; „auto“ = Claude wählt im Plan passend zu Branche und Stimmung, sonst „akzent“.
    #   modus aufbau  – Wort für Wort aufbauen          karaoke – ganze Zeile, gesprochenes Wort in Farbe
    #         wort    – immer nur ein Wort, groß, poppt   rise    – Zeile gleitet weich von unten herein, Wort für Wort
    # Die Platz-Regeln (nicht aufs Gesicht, nichts überlappt, sichere Ränder) gelten für alle gleich; zu lange Zeilen werden kleiner.
    UTW = P.get("untertitel") or {}
    UT_STIL = (UTW.get("stil") or "auto").lower()
    STILE = {
        "akzent":  dict(f="Poppins SemiBold", fs=62, up=False, modus="aufbau", ol=1.8, sh=3, maxw=4, fg="Poppins ExtraBold", it=True, br=0.60),
        "rise":    dict(f="Poppins SemiBold", fs=64, up=False, modus="rise", ol=1.6, sh=4, maxw=4, fg="Poppins ExtraBold", it=True, br=0.60),
        "clean":   dict(f="Poppins ExtraBold", fs=64, up=False, modus="karaoke", ol=2.2, sh=3, maxw=4, fg="Poppins ExtraBold", it=False, br=0.62),
        "hormozi": dict(f="Montserrat", fs=104, up=True, modus="wort", ol=6, sh=4, maxw=1, fg="Montserrat", it=False, br=0.70, bold=True),
        "neon":    dict(f="Poppins ExtraBold", fs=68, up=True, modus="karaoke", ol=3.5, sh=0, maxw=3, fg="Poppins ExtraBold", it=False, br=0.66, glow=True),
        "comic":   dict(f="Bangers", fs=96, up=True, modus="karaoke", ol=6, sh=5, maxw=3, fg="Bangers", it=False, br=0.50, schief=-3),
        "serif":   dict(f="Playfair Display", fs=68, up=False, modus="aufbau", ol=0.8, sh=3, maxw=4, fg="Playfair Display", it=True, br=0.56, bold=True),
        "wide":    dict(f="Bebas Neue", fs=104, up=True, modus="aufbau", ol=1.5, sh=3, maxw=3, fg="Bebas Neue", it=False, br=0.46, sp=5),
        "bold":    dict(f="Montserrat", fs=76, up=True, modus="karaoke", ol=4, sh=3, maxw=3, fg="Montserrat", it=False, br=0.68, bold=True),
        "box":     dict(f="Poppins ExtraBold", fs=60, up=False, modus="karaoke", ol=12, sh=0, maxw=3, fg="Poppins ExtraBold", it=False, br=0.62, box=True),
        "impact":  dict(f="Bebas Neue", fs=132, up=True, modus="karaoke", ol=4, sh=4, maxw=2, fg="Bebas Neue", it=False, br=0.44),
        "mini":    dict(f="Poppins SemiBold", fs=48, up=False, modus="aufbau", ol=1.4, sh=2, maxw=5, fg="Poppins ExtraBold", it=True, br=0.60),
    }
    ST = STILE.get(UT_STIL, STILE["akzent"])
    if UT_STIL not in STILE:
        UT_STIL = "akzent"
    print(f"Untertitel-Stil: {UT_STIL}", flush=True)
    GR = lambda t_: t_.upper() if ST["up"] else t_
    _wf = UTW.get("farbe") if re.fullmatch(r"#[0-9a-fA-F]{6}", UTW.get("farbe") or "") else None
    _h = (_wf or AKZ).lstrip("#")
    _akz_bgr = f"&H{_h[4:6]}{_h[2:4]}{_h[0:2]}&"
    # Schlagwort / gesprochenes Wort in der Farbe des Kunden – aber nur, wo man sie lesen kann (Papaya 13.10.: Gold auf beigem
    # Hemd kaum zu sehen). Je Sprechteil wird die Fläche hinter den Untertiteln gemessen; zu ähnlich → weiß.
    _akz_rgb = np.array([int(_h[i:i + 2], 16) for i in (0, 2, 4)], np.float32)
    GROSS_F = []
    for k in range(len(TALK)):
        farbe_ok = (STUFE != "clean" or bool(_wf) or ST["modus"] in ("karaoke", "wort")) and not ST.get("box")
        if farbe_ok:
            cap = cv2.VideoCapture(f"{CL}/talk{k}.mp4")
            cap.set(cv2.CAP_PROP_POS_FRAMES, int((cap.get(cv2.CAP_PROP_FRAME_COUNT) or 2) / 2))
            ok, fr = cap.read()
            cap.release()
            if ok:
                hg = fr[max(0, UT_Y[k] - 70):UT_Y[k] + 70, 240:840].reshape(-1, 3)[:, ::-1].astype(np.float32).mean(axis=0)
                farbe_ok = float(np.linalg.norm(hg - _akz_rgb)) >= 120
                if not farbe_ok:
                    print(f"Sprechteil {k}: Akzentfarbe hebt sich vom Hintergrund nicht ab → bleibt weiß", flush=True)
        GROSS_F.append(f"\\1c{_akz_bgr}" if farbe_ok else "")
    _hell = (0.299 * _akz_rgb[0] + 0.587 * _akz_rgb[1] + 0.114 * _akz_rgb[2]) > 150
    passt = lambda txt, fs_: int(min(fs_, 960 / max(1.0, len(txt) * ST["br"] + 0.5) )) if len(txt) * fs_ * ST["br"] > 960 else fs_
    cs = lambda s: f"{int(s // 3600)}:{int(s // 60 % 60):02d}:{int(s % 60):02d}.{min(99, int(round((s % 1) * 100))):02d}"
    ev = []
    for k, s in enumerate(TALK):
        ers = s.get("ersetzen") or {}
        ws = []
        for w in s["ws"]:   # Preise „1“ „,99“ zusammenziehen, Schreibweisen des Plans übernehmen
            tx = ers.get(w["t"], ers.get(w["t"].strip(".,!?"), None))
            if tx is not None and w["t"] not in ers:
                tx = tx + (re.search(r"[.,!?]+$", w["t"]) or re.match("", "")).group(0)
            tx = w["t"] if tx is None else tx
            if ws and re.fullmatch(r"\d{1,4}", ws[-1][0]) and re.fullmatch(r",\d{1,2}[.,!?]*", tx):
                ws[-1] = (ws[-1][0] + tx, ws[-1][1], w["e"])
            elif ws and re.match(r"-\w", tx):          # „Thai“ „-Restaurants.“ ist EIN Wort
                ws[-1] = (ws[-1][0] + tx, ws[-1][1], w["e"])
            elif tx:
                ws.append((tx, w["s"], w["e"]))
        gross_plan = s.get("gross")
        gruppen, cur, letzt = [], [], -9.0
        for i, w in enumerate(ws):
            cur.append(w)
            ende = bool(re.search(r"[.,!?:]$", w[0])) or len(cur) >= ST["maxw"] or (i + 1 < len(ws) and ws[i + 1][1] - w[2] > 0.45) \
                or (gross_plan is not None and w[0] in gross_plan)   # ein geplantes Schlüsselwort beendet seine Zeile – dann steht es groß
            if ende or i == len(ws) - 1:
                gruppen.append(cur)
                cur = []
        Y = UT_Y[k]
        for gi, g in enumerate(gruppen):
            g_ende = out_t(k, gruppen[gi + 1][0][1]) if gi + 1 < len(gruppen) else min(teil_ende(k), out_t(k, g[-1][2]) + 0.6)
            wb = g[-1]
            ist_gross = (wb[0] in gross_plan) if gross_plan is not None else (len(norm(wb[0])) >= 6 and re.search(r"[.,!?]$", wb[0]) and wb[1] - letzt >= 1.8)
            if ist_gross and ST["modus"] != "wort":
                letzt = wb[1]
                fs = passt(GR(wb[0]), int(ST["fs"] * (1.4 if STUFE != "clean" else 1.25)))
                kur = "\\i1" if ST["it"] else ""
                ev.append(f"Dialogue: 1,{cs(out_t(k, wb[1]))},{cs(g_ende)},Gross,,0,0,0,,{{\\an5\\pos(540,{Y + 10})\\fs{fs}{kur}\\fad(50,60)\\fscx92\\fscy92\\t(0,110,\\fscx103\\fscy103)\\t(110,200,\\fscx100\\fscy100){GROSS_F[k]}}}{GR(wb[0])}")
                g = g[:-1]
                g_ende = out_t(k, wb[1])
            if not g:
                continue
            zeile = GR(" ".join(x[0] for x in g))
            fsz = passt(zeile, ST["fs"])
            dreh = f"\\frz{ST['schief']}" if ST.get("schief") else ""
            for j in range(len(g)):
                a = out_t(k, g[j][1])
                b = out_t(k, g[j + 1][1]) if j + 1 < len(g) else g_ende
                if b - a < 0.02:
                    continue
                vor = GR(" ".join(x[0] for x in g[:j]))
                wort = GR(g[j][0])
                nach = GR(" ".join(x[0] for x in g[j + 1:]))
                if ST["modus"] == "wort":       # ein Wort, groß, poppt herein; Schlagwörter in Farbe
                    fw = passt(wort, ST["fs"])
                    farbig = GROSS_F[k] if (gross_plan and g[j][0] in gross_plan) or (gross_plan is None and len(norm(g[j][0])) >= 7) else ""
                    ev.append(f"Dialogue: 0,{cs(a)},{cs(b)},Klein,,0,0,0,,{{\\an5\\pos(540,{Y})\\fs{fw}\\fscx80\\fscy80\\t(0,90,\\fscx108\\fscy108)\\t(90,170,\\fscx100\\fscy100){farbig}}}{wort}")
                elif ST["modus"] == "karaoke":  # ganze Zeile steht, das gesprochene Wort leuchtet in der Farbe des Kunden
                    hl = GROSS_F[k] or "\\1c&H5CE1FF&"
                    if ST.get("box"):
                        hl = "\\1c&H101010&" if _hell else "\\1c&HFFFFFF&"
                    ev.append(f"Dialogue: 0,{cs(a)},{cs(b)},Klein,,0,0,0,,{{\\an5\\pos(540,{Y})\\fs{fsz}{dreh}}}" + (vor + " " if vor else "") +
                              f"{{{hl}}}{wort}{{\\r}}{{\\fs{fsz}}}" + (" " + nach if nach else ""))
                else:                            # aufbau / rise: Wort für Wort, der Rest der Zeile unsichtbar (Zeile springt nicht)
                    rein = ""
                    if ST["modus"] == "rise" and j == 0:
                        rein = f"\\move(540,{Y + 26},540,{Y},0,180)\\fad(160,0)"
                    pos = "" if rein else f"\\pos(540,{Y})"
                    ev.append(f"Dialogue: 0,{cs(a)},{cs(b)},Klein,,0,0,0,,{{\\an5{pos}{rein}\\fs{fsz}{dreh}}}" + (vor + " " if vor else "") + wort +
                              (f"{{\\alpha&HFF&}} {nach}" if nach else ""))
    ass = os.path.join(ARB, "untertitel.ass")
    fett = -1 if ST.get("bold") else 0
    if ST.get("box"):      # Kasten: Text auf einem Feld in der Farbe des Kunden (helle Farbe → dunkle Schrift)
        prim, umr, rand, bstil = ("&H00101010" if _hell else "&H00FFFFFF"), f"&H00{_h[4:6]}{_h[2:4]}{_h[0:2]}", ST["ol"], 3
    elif ST.get("glow"):   # Neon: Leuchten in der Farbe des Kunden
        prim, umr, rand, bstil = "&H00FFFFFF", f"&H00{_h[4:6]}{_h[2:4]}{_h[0:2]}", ST["ol"], 1
    else:
        prim, umr, rand, bstil = "&H00FFFFFF", ("&H00000000" if ST["ol"] >= 4 else "&H46000000"), ST["ol"], 1
    open(ass, "w").write("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
                         "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
                         f"Style: Klein,{ST['f']},{ST['fs']},{prim},&H00FFFFFF,{umr},&H6E000000,{fett},0,0,0,100,100,{ST.get('sp', 0)},0,{bstil},{rand},{ST['sh']},5,60,60,0,1\n"
                         f"Style: Gross,{ST['fg']},112,{prim},&H00FFFFFF,{umr},&H6E000000,{-1 if ST.get('bold') or ST['fg'].startswith('Poppins') else 0},0,0,0,100,100,{ST.get('sp', 0) or -1},0,{bstil},{max(2.4, rand)},{max(ST['sh'], 3)},5,40,40,0,1\n\n[Events]\n"
                         "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" + "\n".join(ev) + "\n")

    # Zusammensetzen: Sprechteile (+ Montage) → Einschübe → Grafiken → Untertitel ZULETZT; Ton: Stimme (+ Musik)
    inputs, fc = [], []
    nT = len(TALK)
    if GRADED:   # Bild kommt fertig gegradet aus Resolve, der Ton aus den Sprechteilen
        inputs += ["-i", GRADED]
        for k in range(nT):
            inputs += ["-i", f"{CL}/talk{k}.mp4"]
        fc.append("[0:v]" + ("vignette=PI/6" if STUFE == "premium" else "null") + "[v0]")
        fc.append("".join(f"[{k + 1}:a]" for k in range(nT)) + f"concat=n={nT}:v=0:a=1,loudnorm=I=-14:TP=-1.5:LRA=11,apad=whole_dur={TOTAL:.3f}[stimme]")
        ni, vin = 1 + nT, "v0"
    else:
        for k in range(nT):
            inputs += ["-i", f"{CL}/talk{k}.mp4"]
        for f in mont_files:
            inputs += ["-i", f]
        nV = nT + len(mont_files)
        fc.append("".join(f"[{k}:v]" for k in range(nV)) + f"concat=n={nV}:v=1:a=0[v0]")
        fc.append("".join(f"[{k}:a]" for k in range(nT)) + f"concat=n={nT}:v=0:a=1,loudnorm=I=-14:TP=-1.5:LRA=11,apad=whole_dur={TOTAL:.3f}[stimme]")
        ni, vin = nV, "v0"
    for k, (f, st, du) in enumerate(EIN):
        inputs += ["-i", f]
        fc.append(f"[{ni}:v]setpts=PTS-STARTPTS+{st:.3f}/TB[e{k}]")
        fc.append(f"[{vin}][e{k}]overlay=0:0:enable='between(t,{st:.3f},{st + du:.3f})':eof_action=pass[ve{k}]")
        vin, ni = f"ve{k}", ni + 1
    for k, (png, a, b, xm, y, rand, ohne_weg) in enumerate(G):
        d = b - a
        inputs += ["-loop", "1", "-t", f"{d + 0.1:.3f}", "-i", png]
        ein = "" if ohne_weg else "fade=t=in:st=0:d=0.18:alpha=1,"
        fc.append(f"[{ni}:v]format=rgba,{ein}fade=t=out:st={max(0.2, d - 0.16):.3f}:d=0.16:alpha=1,setpts=PTS-STARTPTS+{a:.3f}/TB[g{k}]")
        weg = "" if ohne_weg else f"+34*pow(1-min(1\\,(t-{a:.3f})/0.28)\\,3)"
        fc.append(f"[{vin}][g{k}]overlay=x={xm:.0f}-w/2:y='{y - rand:.0f}{weg}':eof_action=pass[vg{k}]")
        vin, ni = f"vg{k}", ni + 1
    LOGO = P.get("logo")
    if LOGO and not os.path.isabs(LOGO):
        LOGO = os.path.join(ORD, LOGO)
    if LOGO and os.path.exists(LOGO):       # Logo des Kunden: klein, oben links im sicheren Bereich, leicht durchscheinend
        inputs += ["-loop", "1", "-t", f"{TOTAL:.3f}", "-i", LOGO]
        fc.append(f"[{ni}:v]format=rgba,scale=150:-1:flags=lanczos,colorchannelmixer=aa=0.88[logo]")
        fc.append(f"[{vin}][logo]overlay=70:150:eof_action=pass[vlogo]")
        vin, ni = "vlogo", ni + 1
    elif LOGO:
        print(f"Logo {LOGO} nicht gefunden – ohne Logo", flush=True)
    if STUFE == "premium":                  # feine Fortschrittslinie ganz oben in der Farbe des Kunden
        fc.append(f"color=c=0x{AKZ.lstrip('#')}:s=1080x6:r={FPS}:d={TOTAL:.3f}[bal]")
        fc.append(f"[{vin}][bal]overlay=x='-W+W*t/{TOTAL:.3f}':y=0:eof_action=pass[vbal]")
        vin = "vbal"
    esc = ass.replace("\\", "/").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")
    fc.append(f"[{vin}]ass='{esc}':fontsdir='{FONTS}'[vout]")
    # SOUNDEFFEKTE (Inhaber 13.10.: „Soundeffekte hinzufügen, wenn Sachen eingeblendet werden – passende natürlich“). Jede Einblendung
    # bekommt einen leisen, kurzen Ton: Marken/Emojis „pop“, Preis „kaching“, Listenpunkt „click“, Ort „ding“, Zwischenbild und
    # Schluss-Schild „whoosh“. Leise gemischt (die Stimme bleibt vorn); höchstens einer alle 0,35 s. "sfx": false im Plan schaltet ab.
    SFX_DIR = os.path.join(MASCH, "sfx")
    # GEMESSEN 13.10. (Viral-Tonspur minus Clean-Tonspur): Die Töne lagen 15–20 dB unter der Stimme, die Listen-Klicks bei −50 dB –
    # praktisch unhörbar. Die festen Lautstärke-Faktoren passten nicht zu den Dateien. Jetzt wird jede Ton-Datei gemessen und auf
    # einen festen Pegel gebracht (lauteste 0,25 s): Stimme liegt bei etwa −16 dB, die Töne 8–11 dB darunter – hörbar, aber nie vorn.
    # Bevorzugt werden die eigenen Töne des Inhabers aus material/SOUND EFFEKTE (Pop, Klick, Whoosh), sonst schnitt-maschine/sfx.
    TON = {"emoji": ("pop", -25.0), "marke": ("pop", -25.0), "pille": ("pop", -26.0), "preis": ("kaching", -26.0), "ort": ("ding", -27.0), "liste": ("click", -24.0), "pfeil": ("pop", -25.0)}
    EIGEN = {"pop": "Aodio pop 1.m4a", "click": "Audio Mouse Click.m4a", "whoosh": "Audio woosh 1.m4a"}
    _ton = {}

    def ton_datei(name):
        """Ton ohne Vorlauf-Stille, höchstens 1,5 s, als WAV im Arbeitsordner; liefert (Pfad, gemessener Pegel in dB) oder None."""
        if name not in _ton:
            _ton[name] = None
            quellen = [os.path.join(MASCH, "..", "material", "SOUND EFFEKTE", EIGEN.get(name, "-")), os.path.join(SFX_DIR, name + ".wav")]
            for q_ in quellen:
                if not os.path.exists(q_):
                    continue
                z_ = os.path.join(ARB, f"ton_{name}.wav")
                r_ = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", q_, "-vn", "-ac", "1", "-ar", "48000", "-af",
                                     "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.005,atrim=0:1.5,afade=t=out:st=1.2:d=0.3", z_], capture_output=True)
                if r_.returncode or not os.path.exists(z_):
                    continue
                x_ = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", z_, "-f", "s16le", "-"], capture_output=True).stdout, np.int16).astype(np.float32) / 32768
                if len(x_) < 2400:
                    continue
                w_ = 12000
                pg = max(float(np.sqrt((x_[i:i + w_] ** 2).mean())) for i in range(0, max(1, len(x_) - w_ + 1), 2400)) if len(x_) > w_ else float(np.sqrt((x_ ** 2).mean()))
                if pg > 1e-5:
                    _ton[name] = (z_, 20 * np.log10(pg))
                    break
        return _ton[name]
    sfx = []
    if P.get("sfx", STUFE != "clean"):
        arten = {}
        for k, g in enumerate(P.get("grafiken") or []):
            arten[f"g{k}_"] = g["art"]
        for png, a, b, xm, y, rand, ohne_weg in G:
            if png in STUMM:
                continue
            art = next((v for kk, v in arten.items() if os.path.basename(png).startswith(kk)), "schild" if "schild" in png else None)
            name, vol = TON.get(art, ("whoosh", -27.0) if art == "schild" else (None, 0))
            if name:
                sfx.append((a, name, vol))
        for f_, st, du in EIN:
            sfx.append((max(0.0, st - 0.06), "whoosh", -28.0))
        sfx.sort()
        sfx = [x for i, x in enumerate(sfx) if ton_datei(x[1]) and (i == 0 or x[0] - sfx[i - 1][0] >= 0.35)]
    ton_in = ["[stimme]"]
    for k, (ta, name, vol) in enumerate(sfx):
        pfad_, pegel_ = ton_datei(name)
        inputs += ["-i", pfad_]
        fc.append(f"[{ni}:a]aresample=48000,volume={vol - pegel_:.1f}dB,adelay={int(ta * 1000)}|{int(ta * 1000)}[s{k}]")
        ton_in.append(f"[s{k}]")
        ni += 1
    if len(ton_in) > 1:
        fc.append("".join(ton_in) + f"amix=inputs={len(ton_in)}:normalize=0:duration=first[stimme2]")
    else:
        fc.append("[stimme]anull[stimme2]")
    if MUS:
        inputs += ["-ss", f"{max(0.0, PHASE + BEAT * 8 - (T_TALK % BEAT)):.3f}", "-i", MUS]
        hoch = f"min(0.62,0.11+(t-{T_TALK - 0.25:.3f})*1.4)" if M else "0.11"
        fc.append(f"[{ni}:a]atrim=0:{TOTAL:.3f},asetpts=PTS-STARTPTS,volume='if(lt(t,{T_TALK - 0.25:.3f}),0.11,{hoch})':eval=frame,"
                  f"afade=t=in:st=0:d=0.4,afade=t=out:st={TOTAL - 1.1:.3f}:d=1.1[mus]")
        fc.append("[stimme2][mus]amix=inputs=2:normalize=0,alimiter=limit=0.95[aout]")
    else:
        fc.append("[stimme2]alimiter=limit=0.95[aout]")
    script = os.path.join(ARB, "final.filter")
    open(script, "w").write(";\n".join(fc))
    os.makedirs(os.path.dirname(ZIEL), exist_ok=True)
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-/filter_complex", script, "-map", "[vout]", "-map", "[aout]", "-t", f"{TOTAL:.3f}", "-r", str(FPS),
         "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", ZIEL])
    json.dump({"sprechteile": [(s["clip"], round(s["a"], 3), round(s["a"] + s["d"], 3)) for s in TALK], "gesprochen_s": round(T_TALK, 2), "gesamt_s": round(TOTAL, 2),
               "einschuebe": [(os.path.basename(f), round(a, 2), round(d, 2)) for f, a, d in EIN],
               "grafiken": [(os.path.basename(p), round(a, 2), round(b, 2), int(x), int(y)) for p, a, b, x, y, _, _ in G]},
              open(os.path.join(ARB, "schnittliste.json"), "w"), ensure_ascii=False, indent=1)
    print(f"fertig [{STUFE}]: {ZIEL}  ({TOTAL:.1f} s, davon {T_TALK:.1f} s gesprochen)")


def _bewegung(bilder):
    """Kamerabewegung Bild zu Bild (dx, dy, Drehung) aus Bildpunkten über das ganze Bild; was sich im Bild bewegt (Hände, Menschen),
    wird als Ausreißer verworfen. Maßstab: Breite 360."""
    import cv2
    import numpy as np
    vor, v = None, []
    for g in bilder:
        if vor is not None:
            m = (0.0, 0.0, 0.0)
            pts = cv2.goodFeaturesToTrack(vor, 200, 0.01, 12)
            if pts is not None and len(pts) >= 12:
                nx, st, _ = cv2.calcOpticalFlowPyrLK(vor, g, pts, None)
                ok = st.ravel() == 1
                if ok.sum() >= 8:
                    M, _i = cv2.estimateAffinePartial2D(pts[ok], nx[ok], method=cv2.RANSAC)
                    if M is not None:
                        m = (float(M[0, 2]), float(M[1, 2]), float(np.arctan2(M[1, 0], M[0, 0])))
            v.append(m)
        vor = g
    return np.array(v) if v else np.zeros((0, 3))


def _zittern(v):
    """Zittern = Bewegung abzüglich der ruhigen Kamerafahrt (Schwenk zählt nicht), in px je Bild bei 1080 Breite."""
    import numpy as np
    if len(v) < 20:
        return 0.0
    gl = np.stack([np.convolve(v[:, i], np.ones(9) / 9, "same") for i in (0, 1)], axis=1)
    return float(np.abs(v[:, :2] - gl)[5:-5].mean() * 3.0)


def zittern_messen(quelle, a, d, fps=FPS):
    """Nur messen (für den Schnitt in Resolve, der selbst stabilisiert): Zittern in px je Bild, Schwenks zählen nicht."""
    import numpy as np
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{a:.3f}", "-t", f"{min(d, 6.0):.3f}", "-i", quelle, "-an", "-vf",
                          f"fps={fps:.4f},scale=360:640:force_original_aspect_ratio=increase:flags=bilinear,crop=360:640", "-pix_fmt", "gray",
                          "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    roh = p.stdout.read()
    p.wait()
    n = 360 * 640
    return _zittern(_bewegung([np.frombuffer(roh[i:i + n], np.uint8).reshape(640, 360) for i in range(0, len(roh) - n + 1, n)]))


def stabilisieren(quelle, a, d, ziel, fps=FPS, schwelle=0.3):
    """Misst das Stück; zittert es (Stativ ≈ 0,05 · Gimbal ≈ 0,4 · Handy aus der Hand 1,5–5), wird die Kamerabahn geglättet und das
    Bild entsprechend zurückgeschoben (mit so viel Zoom, dass kein Rand sichtbar wird, höchstens 12 %). Ausgabe 1080×1920 ohne Ton.
    Rückgabe (Zittern vorher, nachher) oder None, wenn nichts zu tun war."""
    import cv2
    import numpy as np
    lesen = lambda w, h, extra: subprocess.Popen(
        ["ffmpeg", "-v", "error", "-ss", f"{a:.3f}", "-t", f"{d:.3f}", "-i", quelle, "-an", "-vf",
         f"fps={fps:.4f},scale={w}:{h}:force_original_aspect_ratio=increase:flags=bilinear,crop={w}:{h}", *extra, "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    p = lesen(360, 640, ["-pix_fmt", "gray"])
    roh = p.stdout.read()
    p.wait()
    klein = [np.frombuffer(roh[i:i + 360 * 640], np.uint8).reshape(640, 360) for i in range(0, len(roh) - 360 * 640 + 1, 360 * 640)]
    v = _bewegung(klein)
    vorher = _zittern(v)
    if vorher <= schwelle:
        return None
    bahn = np.cumsum(np.vstack([np.zeros((1, 3)), v]), axis=0)          # Lage der Kamera je Bild
    n = len(bahn)
    r = max(4, int(fps * 0.6))                                           # Glättung über gut eine Sekunde
    kern = np.exp(-0.5 * (np.arange(-r, r + 1) / (r / 2.2)) ** 2)
    rand = np.pad(bahn, ((r, r), (0, 0)), mode="edge")
    glatt = np.stack([np.convolve(rand[:, i], kern / kern.sum(), "valid") for i in range(3)], axis=1)
    korr = glatt - bahn                                                  # so viel muss jedes Bild verschoben/gedreht werden
    korr[:, :2] *= 3.0                                                   # 360 → 1080
    grenze = np.array([1080 * 0.055, 1920 * 0.055, 0.03])
    korr = np.clip(korr, -grenze, grenze)
    bedarf = max(np.abs(korr[:, 0]).max() / 540, np.abs(korr[:, 1]).max() / 960) + np.abs(korr[:, 2]).max() * 1.2
    zoom = 1.0 + min(0.12, bedarf + 0.005)
    p = lesen(1080, 1920, ["-pix_fmt", "bgr24"])
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "1080x1920", "-framerate", f"{fps:.4f}", "-i", "-",
                            "-c:v", "libx264", "-preset", "fast", "-crf", "10", "-pix_fmt", "yuv420p", ziel], stdin=subprocess.PIPE)
    nach = []
    for i in range(n):
        buf = p.stdout.read(1080 * 1920 * 3)
        if len(buf) < 1080 * 1920 * 3:
            break
        fr = np.frombuffer(buf, np.uint8).reshape(1920, 1080, 3)
        dx, dy, da = korr[i]
        co, si = np.cos(da) * zoom, np.sin(da) * zoom
        M = np.array([[co, -si, 540 - co * 540 + si * 960 + dx], [si, co, 960 - si * 540 - co * 960 + dy]])
        out = cv2.warpAffine(fr, M, (1080, 1920), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT_101)
        enc.stdin.write(out.tobytes())
        nach.append(cv2.cvtColor(cv2.resize(out, (360, 640), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY))
    p.stdout.close()
    p.wait()
    enc.stdin.close()
    enc.wait()
    return vorher, _zittern(_bewegung(nach))


# ───────────────────────────── 5. Selbst ansehen ─────────────────────────────
def pruefen(video, arbeit):
    import math
    d = dauer(video)
    os.makedirs(os.path.join(arbeit, "verify"), exist_ok=True)
    ziel = os.path.join(arbeit, "verify", "uebersicht.jpg")
    run(["ffmpeg", "-v", "error", "-y", "-i", video, "-vf", f"fps=2,scale=200:-1,tile=12x{math.ceil(d * 2 / 12)}", "-frames:v", "1", "-q:v", "4", ziel])
    r = run(["ffmpeg", "-v", "info", "-i", video, "-af", "ebur128", "-vn", "-f", "null", "-"], ok=True).stderr
    laut = re.findall(r"I:\s+([-\d.]+) LUFS", r)
    print(f"{video}: {d:.1f} s, Lautheit {laut[-1] if laut else '?'} LUFS, Übersicht: {ziel}")


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "hoeren":
        hoeren(sys.argv[2], sys.argv[3])
    elif len(sys.argv) >= 3 and sys.argv[1] == "bauen":
        bauen(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "pruefen":
        pruefen(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
