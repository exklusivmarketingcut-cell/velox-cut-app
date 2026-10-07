"""Velox Cut – gemeinsamer KI-Zugang (Gemini) für pipeline.py, montage.py und musikschnitt.py.

Kosten sparen ohne Qualitätsverlust:
  * Schlüssel-Reihenfolge: GEMINI_API_KEY_FREE (Projekt OHNE Zahlungsdaten → kostenloses Kontingent; für Nutzer im EWR
    gelten dort dieselben Datenschutz-Regeln wie bei bezahlter Nutzung) zuerst, GEMINI_API_KEY (Prepay) nur als Reserve.
  * Wichtige Entscheidungen (deep=True: Satzauswahl, Abnahme, Schnittplan) zuerst auf dem großen Flash-Modell, einfache
    Aufgaben auf Flash-Lite (großes Gratis-Kontingent).
  * Tageslimit erreicht (429 „per day“), Guthaben leer (402) oder Modell unbekannt (404) → sofort nächstes Modell bzw.
    nächster Schlüssel, für den Rest des Laufs gemerkt. Minutenlimit (429) → kurz warten, nochmal.
  * Nachdenken sparsam (thinkingLevel minimal/low bzw. thinkingBudget), bei 400 ohne Denk-Einstellung wiederholen.
"""
import json, os, sys, threading, time, urllib.error, urllib.request

FREE = bool(os.environ.get("GEMINI_API_KEY_FREE", "").strip())
KEYS = [k.strip() for k in (os.environ.get("GEMINI_API_KEY_FREE", ""), os.environ.get("GEMINI_API_KEY", "")) if k.strip()]
DEEP = ("gemini-flash-latest", "gemini-3-flash-preview", "gemini-2.5-flash", "gemini-flash-lite-latest", "gemini-3.1-flash-lite")
LIGHT = ("gemini-flash-lite-latest", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite", "gemini-flash-latest")
DEAD = set()            # (Schlüssel-Nr, Modell) bzw. (Schlüssel-Nr, "*") – in diesem Lauf nicht mehr versuchen
TOK = {"aufrufe": 0, "ein": 0, "aus": 0, "denken": 0, "gratis": 0}
EXHAUSTED = False      # alle Schlüssel/Modelle für eine Aufgabe erschöpft (Kontingent/Guthaben) → Auftrag später wiederholen
EXIT_NO_AI = 75        # Exit-Code: „KI-Kontingent erschöpft“ → der Worker legt den Auftrag zurück in die Schlange
_lock = threading.Lock()


def _log(*a):
    print(*a, file=sys.stderr, flush=True)


def available():
    return bool(KEYS)


def _think(model, deep):
    if model.startswith("gemini-2.5"):
        return {"thinkingBudget": 1024 if deep else 0}
    return {"thinkingLevel": "low" if deep else "minimal"}


def call(system, parts, schema, deep=False, timeout=90, temperature=0.2, models=None):
    """Gemini mit JSON-Antwort. None, wenn kein Schlüssel/Modell erreichbar ist (dann gelten die Regeln ohne KI)."""
    if not KEYS:
        return None
    order = models or (DEEP if deep else LIGHT)
    # Reihenfolge (Inhaber 07.10.: „erst kostenlos, und sobald das weg ist, auf den bezahlten wechseln“): je MODELL erst der
    # Gratis-Schlüssel, dann sofort der bezahlte – so entscheidet immer das beste Modell. Vorher wurden mit dem Gratis-Schlüssel
    # erst alle kleineren Modelle durchprobiert (schlechtere Entscheidungen, lange Wartezeiten), bevor der bezahlte dran war.
    for model in order:
        for ki, key in enumerate(KEYS):
            if (ki, "*") in DEAD or (ki, model) in DEAD:
                continue
            think = True
            for attempt in range(4):
                cfg = {"temperature": temperature, "responseMimeType": "application/json", "responseSchema": schema}
                if think:
                    cfg["thinkingConfig"] = _think(model, deep)
                body = json.dumps({"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": parts}],
                                   "generationConfig": cfg}).encode()
                try:
                    req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                                 data=body, headers={"x-goog-api-key": key, "Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        d = json.loads(resp.read())
                    u = d.get("usageMetadata") or {}
                    with _lock:
                        TOK["aufrufe"] += 1
                        TOK["gratis"] += int(FREE and ki == 0)
                        TOK["ein"] += int(u.get("promptTokenCount") or 0)
                        TOK["aus"] += int(u.get("candidatesTokenCount") or 0)
                        TOK["denken"] += int(u.get("thoughtsTokenCount") or 0)
                    TOK["modell"] = model
                    return json.loads("".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"]))
                except urllib.error.HTTPError as e:
                    try:
                        msg = e.read().decode(errors="ignore")[:400]
                    except Exception:  # noqa: BLE001
                        msg = ""
                    _log(f"Gemini {model} (Schlüssel {ki + 1}): HTTP {e.code}")
                    if e.code == 400 and think:          # Denk-Einstellung unbekannt → ohne wiederholen
                        think = False
                        continue
                    if e.code in (401, 402, 403):        # Schlüssel ungültig / Guthaben leer → ganzer Schlüssel raus
                        DEAD.add((ki, "*"))
                        break
                    if e.code == 404:                     # Modell gibt es (für diesen Schlüssel) nicht
                        DEAD.add((ki, model))
                        break
                    if e.code == 429:
                        if "day" in msg.lower() or "perday" in msg.lower().replace("_", ""):
                            DEAD.add((ki, model))         # Tageskontingent aufgebraucht → nächstes Modell
                            break
                        if ki + 1 < len(KEYS) and (ki + 1, "*") not in DEAD and (ki + 1, model) not in DEAD:
                            break                         # Minutenlimit, aber es gibt noch einen Schlüssel → nicht warten, sofort dorthin
                        if attempt < 3:                   # Minutenlimit → kurz warten
                            time.sleep(8 * (attempt + 1))
                            continue
                        break
                    if e.code in (500, 503) and attempt < 3:
                        time.sleep(4 * (attempt + 1))
                        continue
                    break
                except Exception as e:  # noqa: BLE001
                    _log(f"Gemini {model}: {str(e)[:120]}")
                    break
    global EXHAUSTED
    if all((k, "*") in DEAD or (k, m) in DEAD for k in range(len(KEYS)) for m in order):
        EXHAUSTED = True
    return None


def require_or_exit(what=""):
    """Nach wichtigen KI-Schritten aufrufen: ist das Kontingent erschöpft, NICHT ohne KI weiterschneiden (schlechtere
    Videos), sondern mit EXIT_NO_AI abbrechen – der Worker wiederholt den Auftrag, sobald die KI wieder verfügbar ist."""
    if KEYS and EXHAUSTED:
        _log(f"KI-Kontingent erschöpft{(' bei ' + what) if what else ''} – Auftrag wird später wiederholt")
        print("KI_KONTINGENT", flush=True)
        sys.exit(EXIT_NO_AI)
