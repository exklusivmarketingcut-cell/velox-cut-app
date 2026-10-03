"""Velox Cut – Trend-Songs (einmal pro Woche, vom Worker aufgerufen).

Quelle: offizielle Shazam-Charts Deutschland als CSV (tagesaktuell, „Top 200“ + „Discovery“ = gerade aufsteigende Songs).
Was in Deutschland gerade geshazamt wird, läuft fast immer auf TikTok/Reels. Gespeichert werden nur Titel/Interpret –
KEINE Musik. Die Songs werden dem Kunden nur VORGESCHLAGEN: er fügt sie beim Posten in der TikTok-App hinzu (dort über
TikToks Lizenz abgesichert). Die KI ordnet jedem Song Stimmung und passende Videoarten zu.
Ausgabe: schnitt-maschine/trends.json
"""
import csv, datetime, io, json, os, sys, urllib.request

MASCHINE = os.environ.get("VELOX_MASCHINE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "schnitt-maschine"))
OUT = os.path.join(MASCHINE, "trends.json")
KEY = os.environ.get("GEMINI_API_KEY", "").strip()


def chart(kind):
    req = urllib.request.Request(f"https://www.shazam.com/services/charts/csv/{kind}/germany", headers={"User-Agent": "Mozilla/5.0 (Velox Cut Trend-Check)"})
    rows = list(csv.reader(io.StringIO(urllib.request.urlopen(req, timeout=60).read().decode("utf-8-sig"))))
    out = []
    for r in rows:
        if len(r) >= 3 and r[0].strip().isdigit():
            out.append({"rank": int(r[0]), "artist": r[1].strip(), "song": r[2].strip(), "chart": kind})
    return out


def fetch():
    songs, seen = [], set()
    for kind, n in (("discovery", 25), ("top-200", 40)):  # zuerst die Aufsteiger (= viral), dann die großen Hits
        try:
            for s in chart(kind)[:n]:
                key = (s["song"].lower(), s["artist"].lower())
                if key not in seen:
                    seen.add(key)
                    songs.append(s)
        except Exception as e:  # noqa: BLE001
            print(kind, "nicht abrufbar:", e, file=sys.stderr)
    return songs


def classify(songs):
    """Stimmung/Einsatz je Song (einmal pro Woche) – damit die Pipeline zu jedem Video passende Songs vorschlagen kann."""
    if not KEY or not songs:
        return songs
    body = {"contents": [{"parts": [{"text": "Ordne jedem Song kurz Genre, Stimmung und passende Kurzvideo-Arten zu (deutsch, je max. 10 Wörter), z. B. "
                                             "„Deutschrap, gut gelaunt – Food, Vlog, Vorher/Nachher“. Kennst du den Song nicht, schätze aus Titel/Interpret. "
                                             "Antworte als JSON-Liste in derselben Reihenfolge.\n" +
                                             "\n".join(f"{i}. {s['song']} – {s['artist']}" for i, s in enumerate(songs))}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": {"type": "array", "items": {"type": "string"}}}}
    for model in ("gemini-flash-latest", "gemini-2.5-flash"):
        try:
            req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                         data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": KEY})
            moods = json.loads(json.load(urllib.request.urlopen(req, timeout=90))["candidates"][0]["content"]["parts"][0]["text"])
            for s, m in zip(songs, moods):
                s["mood"] = m
            return songs
        except Exception as e:  # noqa: BLE001
            print("Gemini:", str(e)[:120], file=sys.stderr)
    return songs


if __name__ == "__main__":
    songs = classify(fetch())
    if not songs:
        sys.exit("keine Trends abrufbar – alte Liste bleibt")
    json.dump({"stand": datetime.date.today().isoformat(), "quelle": "Shazam-Charts Deutschland (Discovery + Top 200)", "songs": songs},
              open(OUT, "w"), ensure_ascii=False, indent=1)
    print(f"{len(songs)} Trend-Songs gespeichert ({sum(1 for x in songs if x['chart'] == 'discovery')} Aufsteiger)")
