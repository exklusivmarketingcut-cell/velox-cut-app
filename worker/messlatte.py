"""Velox Cut – Messlatte (11.10.2026): misst ein FERTIGES Video an festen Maßstäben, unabhängig vom Schnittprogramm.

  venv/bin/python app/worker/messlatte.py <ordner mit clip-1.mp4 + clip-1.woerter.json> [stil] [--json]

Teil 1 – Schnitt (gilt für JEDEN Stil gleich): Das fertige Video wird neu abgehört und mit dem verglichen, was laut
Schnitt drin sein soll.
  fehlt      Wort soll drin sein, ist aber nicht (ganz) zu hören  → zu früh/zu spät geschnitten
  rest       etwas ist zu hören, das nicht drin sein soll         → Wortrest, stehengebliebenes Stück
  fueller    gehörte Füll-Laute (äh, ähm …)
  luecken    Sprechpausen ≥ 0,45 s mitten im Video
Teil 2 – Stil-Stufe (Skala 1–10; Clean 1–3, Viral 3–6, Premium 6–10): was der Stil haben MUSS und NICHT haben darf,
abgelesen aus dem Protokoll des Laufs (jobs.jsonl, letzter Eintrag) – siehe notizen/velox-messlatte.md."""
import difflib, json, os, re, subprocess, sys
import mlx_whisper

WHISPER = os.environ.get("VELOX_WHISPER", "mlx-community/whisper-large-v3-turbo")
FILLER = {"äh", "ähm", "ähh", "ähmm", "öh", "öhm", "hm", "hmm", "mhm", "ehm", "uhm", "erm"}   # ohne „um“/„eh“ – das sind deutsche Wörter
ZAHL = {"null": "0", "eins": "1", "ein": "1", "eine": "1", "einen": "1", "zwei": "2", "drei": "3", "vier": "4", "fünf": "5", "sechs": "6",
        "sieben": "7", "acht": "8", "neun": "9", "zehn": "10", "elf": "11", "zwölf": "12", "zwanzig": "20", "dreißig": "30",
        "fünfzig": "50", "hundert": "100", "tausend": "1000"}
norm = lambda s: re.sub(r"[^\wäöüß]", "", str(s).lower())
ZW = re.compile(r"^(\d+|null|eins?|eine[nmr]?|zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn|elf|zwölf|\w*zehn|\w*zig|\w*ßig|\w*hundert\w*|\w*tausend\w*)$")
nur_zahlen = lambda ws: bool(ws) and all(ZW.match(w) for w in ws)   # „tausend fünfhundert“ / „1 500“ / „1500“ = dieselbe Zahl, nur anders geschrieben
gleich = lambda a, b: a == b or ZAHL.get(a, a) == ZAHL.get(b, b) or (len(a) > 4 and len(b) > 4 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.8)


_zus = lambda ws: "".join(ws).replace("es", "s")   # „gibt's“ / „gibt es“, „drum herum“ / „drumherum“ = dasselbe, nur anders getrennt


def hoeren(wav, prompt):
    r = mlx_whisper.transcribe(wav, path_or_hf_repo=WHISPER, word_timestamps=True, condition_on_previous_text=False,
                               temperature=0.0, hallucination_silence_threshold=2.0, initial_prompt=prompt)
    return [(norm(w["word"]), float(w["start"]), float(w["end"])) for sg in r["segments"] for w in sg.get("words", []) if norm(w["word"])]


def schnitt(mp4, soll):
    wav = mp4[:-4] + ".mess.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", mp4, "-vn", "-ac", "1", "-ar", "16000", wav], check=True)
    h1 = hoeren(wav, None)
    h2 = hoeren(wav, "Äh... ähm... also... äh, ja, genau, ähm.")
    os.remove(wav)
    S = [norm(w[0]) for w in soll if norm(w[0])]
    St = [float(w[1]) for w in soll if norm(w[0])]
    H = [w[0] for w in h1 if w[0] not in FILLER]
    Ht = [w[1] for w in h1 if w[0] not in FILLER]
    fehlt, rest = [], []
    for tag, a0, a1, b0, b1 in difflib.SequenceMatcher(None, S, H, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        a, b = S[a0:a1], H[b0:b1]
        if _zus(a) == _zus(b):            # nur anders getrennt („drum herum“ / „drumherum“)
            continue
        if len(a) == len(b) and all(gleich(x, y) for x, y in zip(a, b)):   # andere Schreibweise desselben Worts
            continue
        if nur_zahlen(a) and nur_zahlen(b):
            continue
        t = St[a0] if a0 < len(St) else (St[-1] if St else 0.0)
        if a and not b:
            fehlt.append((round(t, 1), " ".join(a), ""))
        elif b and not a:
            rest.append((round(Ht[b0], 1), "", " ".join(b)))
        else:   # ersetzt: zählt als „fehlt“ (Wort beschädigt), der gehörte Text steht daneben
            fehlt.append((round(t, 1), " ".join(a), " ".join(b)))
    # Gegenprobe (11.10.): Beim Abhören des ganzen Videos lässt die Erkennung gelegentlich 1–2 s einfach aus – das sah aus wie
    # „fünf Wörter fehlen + Lücke“, obwohl alles zu hören war. Jede Fundstelle wird deshalb einzeln nachgehört (6-s-Ausschnitt);
    # sie zählt nur, wenn sie sich dort bestätigt.
    def ausschnitt(t):
        a0 = max(0.0, t - 2.5)
        tmp = mp4[:-4] + ".stelle.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a0:.2f}", "-t", "6", "-i", mp4, "-vn", "-ac", "1", "-ar", "16000", tmp], check=True)
        hw = [(w, a0 + s_, a0 + e_) for w, s_, e_ in hoeren(tmp, None)]
        os.remove(tmp)
        return a0, hw

    def bestaetigt(t):
        """Zeigt auch der einzeln abgehörte Ausschnitt um t eine Abweichung (±1 s um die Stelle, nicht am Rand des Ausschnitts)?"""
        a0, hw = ausschnitt(t)
        Sw = [(w_, t_) for w_, t_ in zip(S, St) if a0 + 0.3 <= t_ <= a0 + 5.5]
        H2 = [w for w, _, _ in hw if w not in FILLER]
        if len(Sw) < 3 or len(H2) < 3:
            return True, a0
        for tag, x0, x1, y0, y1 in difflib.SequenceMatcher(None, [w for w, _ in Sw], H2, autojunk=False).get_opcodes():
            if tag == "equal":
                continue
            a, b = [w for w, _ in Sw[x0:x1]], H2[y0:y1]
            if _zus(a) == _zus(b) or (len(a) == len(b) and all(gleich(x, y) for x, y in zip(a, b))) or (nur_zahlen(a) and nur_zahlen(b)):
                continue
            if x0 == 0 or x1 >= len(Sw) or y0 == 0 or y1 >= len(H2):   # Rand des Ausschnitts: dort ist das Wort nur angeschnitten
                continue
            if abs(Sw[min(x0, len(Sw) - 1)][1] - t) <= 1.0:
                return True, a0
        return False, a0

    fehlt2, rest2, weg = [], [], []
    for liste, ziel in ((fehlt, fehlt2), (rest, rest2)):
        for t, a, b in liste:
            echt, a0 = bestaetigt(t)
            if echt:
                ziel.append((t, a, b))
            else:
                weg.append((a0, a0 + 6.0))
    fehlt, rest = fehlt2, rest2
    # dasselbe Wort einmal „fehlt“ und direkt daneben „zu viel“ = nur verrutscht gehört, kein Fehler
    for f_ in list(fehlt):
        r_ = next((r for r in rest if r[2] == f_[1] and not f_[2] and abs(r[0] - f_[0]) <= 0.6), None)
        if r_:
            fehlt.remove(f_)
            rest.remove(r_)
    fueller = sorted({(round(s, 1), w) for w, s, _ in h1 + h2 if w in FILLER})
    dur = max([w[2] for w in h1] + [0.1])
    luecken = [(round(h1[i][2], 1), round(h1[i + 1][1] - h1[i][2], 2)) for i in range(len(h1) - 1)
               if h1[i + 1][1] - h1[i][2] >= 0.45 and 1.0 < h1[i][2] < dur - 1.0]
    lu2 = []
    for t, d_ in luecken:
        if any(x <= t <= y for x, y in weg):   # dort hat die Gegenprobe durchgehend Sprache gehört
            continue
        a0, hw = ausschnitt(t)
        if any(hw[i + 1][1] - hw[i][2] >= 0.45 and abs(hw[i][2] - t) < 1.2 for i in range(len(hw) - 1)):
            lu2.append((t, d_))
    luecken = lu2
    n = max(1, len(S))
    fehler = len(fehlt) + len(rest) + len(fueller) + len(luecken)
    return {"woerter": len(S), "sekunden": round(dur, 1), "fehlt": fehlt, "rest": rest, "fueller": fueller, "luecken": luecken,
            "fehler": fehler, "fehler_je_100": round(100 * fehler / n, 1)}


# Stil-Stufen: (Name, Punkte, Prüfung auf dem Protokoll-Eintrag d) – „muss“ gibt Punkte, „darf nicht“ zieht die Stufe auf ihren Anfang
def stufe(stil, d):
    p2 = d.get("premium2") or {}
    punches, karten, fx = d.get("zoom_punches") or [], d.get("premium_karten") or [], d.get("fx") or []
    dur = float(d.get("out_s") or 0) or 1.0
    ok, nicht = [], []
    muss = lambda name, bed: (ok if bed else nicht).append(name)
    muss("Untertitel vollständig", (d.get("caption_words") or 0) > 0)
    muss("Titel im Bild", not d.get("titel_weggelassen"))
    if stil == "clean":      # 1–3: ruhig. Schnitt + Untertitel + Titel; höchstens Orts-Marke/Schlusskarte
        muss("keine Zoom-Punches", not punches)
        muss("keine Emojis/Sounds", not fx)
        muss("höchstens 2 ruhige Marken", len(karten) <= 2)
        lo, hi = 1, 3
    elif stil == "viral":    # 3–6: laut. Zooms mit Grund, Sounds/Emojis, Stichwort-Karten
        muss("Zooms vorhanden (mind. 1 je 20 s)", len(punches) >= max(1, dur / 20))
        muss("Zooms nicht dichter als alle 2,5 s", all(b - a >= 2.4 for a, b in zip(punches, punches[1:])))
        muss("Sounds/Emojis (mind. 2)", len(fx) >= 2)
        muss("mind. 1 Stichwort-Karte oder Zähler", len(karten) >= 1)
        lo, hi = 3, 6
    else:                    # 6–10: Baukasten. Viele verschiedene Grafiken, mind. ein großes Element
        el = p2.get("elemente") or []
        muss("neuer Baukasten benutzt", bool(p2))
        muss("mind. 1 Grafik je 8 s", len(el) >= max(4, dur / 8))
        muss("mind. 4 verschiedene Arten", len(set(el)) >= 4)
        muss("mind. 1 großes Element (Vollbild/Rahmen/dahinter)", any(e in ("full", "behind", "steps", "checks", "funnel", "broll", "phone") for e in el))
        lo, hi = 6, 10
    wert = round(lo + (hi - lo) * len(ok) / max(1, len(ok) + len(nicht)), 1)
    return {"stil": stil, "stufe": wert, "bereich": [lo, hi], "erfuellt": ok, "nicht_erfuellt": nicht}


if __name__ == "__main__":
    ordner = sys.argv[1]
    stil = next((a for a in sys.argv[2:] if a in ("clean", "viral", "premium")), None)
    soll = json.load(open(os.path.join(ordner, "clip-1.woerter.json")))
    res = {"schnitt": schnitt(os.path.join(ordner, "clip-1.mp4"), soll)}
    log = os.environ.get("VELOX_JOBLOG")
    if log and os.path.exists(log):
        d_ = json.loads(open(log).read().strip().splitlines()[-1])
        vs = [w for w in (d_.get("missing") or []) if not nur_zahlen([norm(w)]) and len(norm(w)) >= 5 and not w.isupper()]   # (ohne Zahlwörter, Kürzel und Kurzwörter wie „es“ – die verhört die Erkennung ständig)
        # Wörter, die drin sein sollten, aber schon nach dem Schnitt nicht zu hören waren
        _schon = {x for f_ in res["schnitt"]["fehlt"] for x in f_[1].split()}   # nicht doppelt zählen, was schon unter „fehlt“ steht
        vs = [w for w in vs if norm(w) not in _schon]
        res["schnitt"]["verschluckt"] = vs
        res["schnitt"]["fehler"] += len(vs)
        res["schnitt"]["fehler_je_100"] = round(100 * res["schnitt"]["fehler"] / max(1, res["schnitt"]["woerter"] + len(vs)), 1)
        if stil:
            res["stil"] = stufe(stil, d_)
    if "--json" in sys.argv:
        print(json.dumps(res, ensure_ascii=False))
    else:
        s = res["schnitt"]
        print(f"SCHNITT: {s['woerter']} Wörter, {s['sekunden']} s → {s['fehler']} Fehler ({s['fehler_je_100']} je 100 Wörter)")
        for k in ("fehlt", "rest", "fueller", "luecken", "verschluckt"):
            if s.get(k):
                print(f"  {k}: {s[k][:8]}")
        if "stil" in res:
            t = res["stil"]
            print(f"STIL {t['stil']}: Stufe {t['stufe']} (Bereich {t['bereich'][0]}–{t['bereich'][1]}); fehlt: {t['nicht_erfuellt'] or '–'}")
