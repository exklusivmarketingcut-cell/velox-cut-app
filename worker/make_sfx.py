"""Velox Cut – eigene Soundeffekte erzeugen (rein synthetisch → gehören uns, keine Lizenzfragen).

Aufruf:  python worker/make_sfx.py  →  schreibt WAVs nach schnitt-maschine/sfx/
whoosh (Zoom/Übergang), pop (Emoji erscheint), ding (Pointe/Bewertung), kaching (Geld/Preis), click (Aufzählung), riser (Hook).
"""
import os
import wave

import numpy as np

SR = 48000
OUT = os.environ.get("VELOX_SFX", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "schnitt-maschine", "sfx"))
rng = np.random.default_rng(7)


def t(d):
    return np.arange(int(SR * d)) / SR


def norm(x, peak=0.7):
    return x / (np.max(np.abs(x)) + 1e-9) * peak


def lowpass_sweep(x, f0, f1):
    """Einpoliger Tiefpass mit wanderndem Grenzwert (klingt wie Luft, die vorbeizieht)."""
    y, prev = np.zeros_like(x), 0.0
    fc = np.geomspace(f0, f1, len(x))
    a = 1 - np.exp(-2 * np.pi * fc / SR)
    for i, v in enumerate(x):
        prev += a[i] * (v - prev)
        y[i] = prev
    return y


def whoosh():
    d = 0.42
    n = rng.standard_normal(len(t(d)))
    up = lowpass_sweep(n, 400, 6000)
    env = np.sin(np.linspace(0, np.pi, len(n))) ** 1.6
    return norm((up - lowpass_sweep(up, 200, 400)) * env, 0.6)


def pop():
    x = t(0.09)
    f = 950 * np.exp(-x * 28) + 260
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-x * 45)
    return norm(s, 0.75)


def ding():
    x = t(0.9)
    s = sum(a * np.sin(2 * np.pi * f * x) for f, a in ((1568, 1.0), (3136, 0.35), (4704, 0.12))) * np.exp(-x * 5.5)
    s[: int(SR * 0.004)] *= np.linspace(0, 1, int(SR * 0.004))
    return norm(s, 0.55)


def kaching():
    x = t(0.75)
    bell = lambda f, off: np.concatenate([np.zeros(int(SR * off)), (np.sin(2 * np.pi * f * x) + 0.4 * np.sin(2 * np.pi * f * 2.7 * x)) * np.exp(-x * 7)])[: len(x)]
    ch = rng.standard_normal(len(x)) * np.exp(-x * 60) * 0.5
    return norm(bell(2093, 0.0) + bell(2637, 0.07) + ch, 0.55)


def click():
    x = t(0.03)
    return norm(rng.standard_normal(len(x)) * np.exp(-x * 400), 0.5)


def riser():
    x = t(1.1)
    f = np.geomspace(180, 1400, len(x))
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.35
    noise = lowpass_sweep(rng.standard_normal(len(x)), 300, 8000) * 0.8
    env = np.linspace(0, 1, len(x)) ** 2
    env[-int(SR * 0.03):] *= np.linspace(1, 0, int(SR * 0.03))
    return norm((tone + noise) * env, 0.5)


def save(name, x):
    os.makedirs(OUT, exist_ok=True)
    fade = int(SR * 0.005)
    x = x.copy()
    x[-fade:] *= np.linspace(1, 0, fade)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(os.path.join(OUT, f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


if __name__ == "__main__":
    for name, fn in (("whoosh", whoosh), ("pop", pop), ("ding", ding), ("kaching", kaching), ("click", click), ("riser", riser)):
        save(name, fn())
    print("Soundeffekte erzeugt in", os.path.abspath(OUT))
