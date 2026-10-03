"""Velox Cut – Emoji-Bilder aus der Apple-Emoji-Schrift des Macs erzeugen (für Viral-Akzente).
Aufruf: python worker/make_emoji.py  →  schnitt-maschine/emoji/<codepoint>.png (160 px, transparent)
Hinweis: Apple-Emojis gehören Apple; auf Wunsch des Inhabers verwendet. Austauschbar über VELOX_EMOJI (z. B. Fluent/Noto)."""
import os, re, sys
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
OUT = os.environ.get("VELOX_EMOJI", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "schnitt-maschine", "emoji"))
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline.py"), encoding="utf-8").read()
emojis = dict(re.findall(r'"([^"\x00-\x7f]+)": "([0-9a-f]+)"', src[src.index("EMOJIS = {"):src.index("SFX_NAMES")]))
font = ImageFont.truetype("/System/Library/Fonts/Apple Color Emoji.ttc", 160)  # Apple-Emoji-Schrift kennt nur feste Größen (160)
os.makedirs(OUT, exist_ok=True)
for ch, code in emojis.items():
    # quadratisch und NICHT auf den Inhalt beschnitten: alle Emojis wirken gleich groß; im Video werden sie nur
    # verkleinert (nie vergrößert) → scharf wie auf dem iPhone
    img = Image.new("RGBA", (176, 176), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((88, 88), ch, font=font, embedded_color=True, anchor="mm")
    img.save(os.path.join(OUT, f"{code}.png"))
print(len(emojis), "Emojis erzeugt in", os.path.abspath(OUT))
