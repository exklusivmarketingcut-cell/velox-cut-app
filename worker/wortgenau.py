"""Wortgenaue Zuordnung Text → Ton (CTC Forced Alignment, torchaudio MMS_FA; läuft lokal, kostenlos).

Die Spracherkennung liefert Wortzeiten, die oft 0,2–0,4 s danebenliegen (Wortanfänge zu früh, Füllwörter verschluckt).
Dieses Modul bekommt den erkannten Text und legt jedes Wort auf ~20 ms genau in den Ton. Eingeführt 09.10.2026 nach den
Novus-Videos des Inhabers („zu früh / zu spät gecuttet“, Füllwörter mitten im Satz).
Aufruf: zeiten = wortzeiten(wav_pfad, ["Hallo", "zusammen", ...])  →  [(start, ende, güte), ...] oder None, wenn es nicht geht.
"""
import re
import wave

EIN = ["null", "ein", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun", "zehn", "elf", "zwölf", "dreizehn",
       "vierzehn", "fünfzehn", "sechzehn", "siebzehn", "achtzehn", "neunzehn"]
ZEHN = ["", "", "zwanzig", "dreißig", "vierzig", "fünfzig", "sechzig", "siebzig", "achtzig", "neunzig"]


def zahl(n):
    n = int(n)
    if n < 20:
        return EIN[n] if n != 1 else "eins"
    if n < 100:
        return (EIN[n % 10] + "und" if n % 10 else "") + ZEHN[n // 10]
    if n < 1000:
        return EIN[n // 100] + "hundert" + (zahl(n % 100) if n % 100 else "")
    if n < 10000:
        return EIN[n // 1000] + "tausend" + (zahl(n % 1000) if n % 1000 else "")
    return ""


def roman(w):
    """Wort in die Buchstaben des Modells umschreiben (a–z und '), Zahlen ausgeschrieben."""
    w = w.lower()
    if re.fullmatch(r"[\d.,:\-–]+[.,!?]?", w):
        w = "".join(zahl(z) for z in re.findall(r"\d+", w.replace(".", "") if w.count(".") and len(w) > 4 else w))
    else:
        w = re.sub(r"\d+", lambda m: zahl(m.group(0)), w)
    for a, b in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss"), ("é", "e"), ("è", "e"), ("á", "a"), ("ç", "c")):
        w = w.replace(a, b)
    return re.sub(r"[^a-z']", "", w)


def wortzeiten(wav_path, words, max_s=900):
    """words: Wörter in Sprechreihenfolge. Rückgabe [(start, ende, güte)] in Sekunden – oder None (zu lang, Fehler)."""
    try:
        import numpy as np
        import torch
        import torchaudio
        from torchaudio.pipelines import MMS_FA as B
        w = wave.open(wav_path)
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getnframes() / 16000 > max_s or not words:
            return None
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        model = B.get_model(with_star=True).eval()
        D = B.get_dict(star="*")
        x = torch.from_numpy(a)[None]
        with torch.inference_mode():
            ems = []
            step, ov, pos, n = 30 * 16000, 2 * 16000, 0, x.shape[1]
            while pos < n:   # in 30-s-Stücken mit Überlappung (Arbeitsspeicher)
                seg = x[:, max(0, pos - ov):min(n, pos + step + ov)]
                e, _ = model(seg)
                e = torch.log_softmax(e, -1)[0]
                r = seg.shape[1] / e.shape[0]
                lo = int(round((pos - max(0, pos - ov)) / r))
                ems.append(e[lo:lo + int(round(min(step, n - pos) / r))])
                pos += step
            em = torch.cat(ems)[None]
        del model
        toks, spans = [], []
        for wd in words:
            ids = [D[c] for c in roman(wd) if c in D] or [D["*"]]
            spans.append((len(toks), len(toks) + len(ids)))
            toks += ids
        if len(toks) >= em.shape[1]:
            return None
        al, sc = torchaudio.functional.forced_align(em, torch.tensor([toks]), blank=0)
        ts = torchaudio.functional.merge_tokens(al[0], sc[0].exp())
        ratio = len(a) / em.shape[1] / 16000
        out = []
        for i, j in spans:
            t = ts[i:j]
            out.append((round(t[0].start * ratio, 3), round(t[-1].end * ratio, 3), round(float(np.mean([q.score for q in t])), 2)))
        return out
    except Exception as ex:  # noqa: BLE001
        print(f"[wortgenau] nicht möglich: {ex}", flush=True)
        return None
