"""Velox Cut – Premium-Grafiken (Stand 09.10.2026).

Aus dem fertigen Schnitt (Video ohne Untertitel) und den Wortzeiten macht dieses Modul das Premium-Video:
  1. Die KI liest den Text und wählt, welche Grafik-Bausteine wo passen (Vollbild-Karte, Sprecher als Karte, Text hinter der
     Person, Zähler, Listen, Trichter, Chat, Suchleiste …). Regel des Inhabers: Abwechslung – nicht jedes Video bekommt dieselben.
  2. Alles wird geprüft (Längen, Überschneidungen, Zeiten auf dem gesprochenen Wort) und als Eigenschaften-Datei geschrieben.
  3. Gezeichnet wird mit dem Remotion-Baukasten in worker/premium/ (derselbe, mit dem die Novus-Videos des Inhabers entstanden).
Vorbilder: TikToks, die der Inhaber am 09.10. geschickt hat (Auswertung in notizen/velox-schnitt-feedback.md).
"""
import hashlib, json, math, os, random, re, shutil, subprocess

HIER = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.join(HIER, "premium")                                   # Quelle (SSD)
CACHE = os.path.expanduser("~/Library/Caches/velox-premium")           # dort wird gerendert (exFAT taugt dafür nicht)
MODULES = os.path.expanduser("~/vaka-remotion/node_modules")           # Remotion ist dort installiert
MATTE = os.path.join(CACHE, "matte")
ICONS = ["fork", "build", "pin", "send", "check", "x", "user", "chat", "phone", "doc", "euro", "cam", "laptop", "mobile", "bot",
         "insta", "search", "clock", "up", "star", "hand", "bulb"]
ARTEN = ["chips", "zahl", "schritte", "haken", "trichter", "nummer", "wort", "voll_wort", "voll_zahl", "chat", "suche", "anruf", "dahinter"]
norm = lambda s: re.sub(r"[^a-zäöüß0-9]", "", s.lower())


def _kurz(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else ""


def _zahlen(res):
    """zahl/zahl2 kommen als Text (10.10.: beim Typ „number“ schrieb das Modell endlose Nachkommastellen – 65.000 Token,
    Minuten Wartezeit, unbrauchbar). Hier werden sie wieder Zahlen: „1.500“, „1500 €“, „12,5“ → 1500 / 1500 / 12.5."""
    for g in (res or {}).get("grafiken") or []:
        for k in ("zahl", "zahl2"):
            v = g.get(k)
            if isinstance(v, str):
                m = re.search(r"\d[\d.\s]*(?:,\d+)?", v)
                z = None
                if m:
                    t = m.group(0).strip()
                    t = t.replace(" ", "")
                    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", t) or "," in t:
                        t = t.replace(".", "").replace(",", ".")
                    try:
                        z = float(t)
                        z = int(z) if z == int(z) else round(z, 2)
                    except ValueError:
                        z = None
                if z is None:
                    g.pop(k, None)
                else:
                    g[k] = z


_ZW = {"null": 0, "ein": 1, "eins": 1, "eine": 1, "einen": 1, "einem": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6, "sieben": 7,
       "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12, "zwanzig": 20, "dreißig": 30, "vierzig": 40, "fünfzig": 50,
       "sechzig": 60, "siebzig": 70, "achtzig": 80, "neunzig": 90, "hundert": 100, "tausend": 1000, "million": 1000000}


def _nur_gesagtes(res, T, log=print):
    """Zähler dürfen nur Zahlen zeigen, die im Video GESAGT werden (Test 12.10.: Zähler „4 – 2.024“ in einem Video, in dem nur
    „drei oder vier“ fiel). Eine Zahl-Grafik, deren Zahl nicht im Text vorkommt, entfällt; ein erfundenes zahl2 wird entfernt."""
    gesagt = set()
    for w, _, _ in T:
        for z in re.findall(r"\d+(?:[.,]\d+)?", w.replace(".", "") if re.fullmatch(r"\d{1,3}(\.\d{3})+\D*", w) else w):
            try:
                gesagt.add(float(z.replace(",", ".")))
            except ValueError:
                pass
        k = re.sub(r"[^a-zäöüß]", "", w.lower())
        if k in _ZW:
            gesagt.add(float(_ZW[k]))
        for a_, b_ in ((k[:-7], 100), (k[:-7], 1000)):   # „fünfhundert“, „dreitausend“
            if (k.endswith("hundert") and b_ == 100 or k.endswith("tausend") and b_ == 1000) and a_ in _ZW:
                gesagt.add(float(_ZW[a_] * b_))
    # zusammengesetzt gesprochene Zahlen („tausend fünfhundert“, „drei- vierhundert“) lassen sich so nicht sicher prüfen →
    # Summen/Produkte benachbarter Zahlwörter zulassen
    einzel = sorted(gesagt)
    for x in einzel:
        for y in einzel:
            gesagt.update({x + y, x * y})
    out = []
    for g in (res or {}).get("grafiken") or []:
        if g.get("art") in ("zahl", "voll_zahl"):
            if isinstance(g.get("zahl2"), (int, float)) and float(g["zahl2"]) not in gesagt:
                g.pop("zahl2", None)
            if not isinstance(g.get("zahl"), (int, float)) or float(g["zahl"]) not in gesagt:
                log(f"Premium-Grafiken: Zähler „{g.get('zahl')}“ entfällt – diese Zahl wird im Video nicht gesagt")
                continue
        out.append(g)
    if res is not None:
        res["grafiken"] = out


def plane(T, ki_call, title, seed, log=print):
    """KI wählt die Grafiken. T = [(wort, start, ende)]. Rückgabe: dict mit hook, betont, grafiken, schluss – oder None."""
    rnd = random.Random(seed)
    # Abwechslung: je Video steht nur ein Teil des Baukastens zur Wahl
    kern = ["chips", "zahl", "schritte", "haken"]
    extra = rnd.sample(["trichter", "nummer", "wort", "chat", "suche", "anruf"], 3)
    gross = rnd.sample(["voll_wort", "voll_zahl", "dahinter"], 2) + ["dahinter"]
    erlaubt = sorted(set(kern + extra + gross))
    text = " ".join(f"[{i}]{w}" for i, (w, _, _) in enumerate(T))
    system = (
        "Du bist Motion-Designer für hochwertige Kurzvideos (TikTok/Reels). Du bekommst den gesprochenen Text eines fertig "
        "geschnittenen Videos, jedes Wort mit Nummer. Plane Grafiken, die GENAU beim gesprochenen Wort erscheinen und das Gesagte "
        "sichtbar machen. Regeln: (1) Nichts erfinden – nur Zahlen, Namen und Fakten, die im Text vorkommen. (2) Texte in Grafiken "
        "sehr kurz (1–3 Wörter), in der Sprache des Videos. (3) Zwischen zwei Grafiken darf Luft sein; nicht jede Sekunde belegen, "
        "aber im Schnitt alle 4–7 Sekunden etwas Neues. (4) Grafiken überschneiden sich nicht: jede hat Wort-Nummer 'von' "
        "(erscheint) und 'bis' (verschwindet nach diesem Wort). (5) Abwechslung: verwende mindestens 4 verschiedene Arten, keine "
        "Art öfter als zweimal. (6) 'karte': true nur bei 'schritte', 'haken' oder 'trichter', höchstens zweimal – dann schrumpft "
        "der Sprecher in eine Karte und die Grafik steht groß darunter. (7) Höchstens zwei Vollbild-Karten und genau EIN "
        "'dahinter' (ein starkes Wort, höchstens 10 Buchstaben, in Großbuchstaben, hinter der Person). "
        f"Erlaubte Arten in diesem Video: {', '.join(erlaubt)}. Bedeutung: chips = 1–2 kleine Karten mit Symbol (texte: text, "
        "sub, icon, wort, optional ok true/false für Haken/Kreuz); zahl = Zähler (zahl, optional zahl2 für eine Spanne, einheit, "
        "gross = Beschriftung); schritte = Ablauf mit 2–5 Stationen (kicker = Überschrift, texte mit wort); haken = Liste mit "
        "bis zu 3 Punkten (texte: text, wort, ok); trichter = bis zu 3 Stufen von groß nach klein (texte: text = Beschriftung, "
        "sub = Zahl als Text, wort); zahl und zahl2 immer als kurze Ziffernfolge ohne Einheit, z. B. „1500“ oder „12,5“; nummer = nummerierter Punkt (zahl = 1/2/3, gross = Titel, akzent = kurze Erklärung); "
        "wort = ein großes Schlagwort auf der Bühne (gross, kicker); voll_wort = Vollbild-Karte mit Schlagzeile (gross in "
        "Großbuchstaben, höchstens 2 Zeilen mit \\n, akzent = ein Wort in Schreibschrift, kicker); voll_zahl = Vollbild-Karte "
        "mit Zahl (zahl, zahl2, einheit, gross = Beschriftung, kicker); chat = 1–2 kurze Chat-Nachrichten (texte: text); suche = "
        "Suchleiste, in die getippt wird (gross = Suchtext); anruf = eingehender Anruf (gross = Name, akzent = Zeile darunter); "
        "dahinter = großes Wort hinter der Person (gross). "
        f"Symbole (icon): {', '.join(ICONS)}. 'betont': Nummern der 8–20 wichtigsten Wörter (werden im Untertitel hervorgehoben). "
        "'hook': großes Einstiegs-Element in den ersten 2–3 Sekunden (art: dahinter, voll_wort oder wort; gross; klein). "
        "'schluss': kurze Zeile für den Abspann (zeile + ein hervorgehobenes wort), z. B. 'Folg uns für' + 'mehr'.")
    schema = {"type": "object", "properties": {
        "hook": {"type": "object", "properties": {"art": {"type": "string"}, "gross": {"type": "string"}, "klein": {"type": "string"}, "bis": {"type": "integer"}}, "required": ["art", "gross", "bis"]},
        "betont": {"type": "array", "maxItems": "24", "items": {"type": "integer"}},
        "grafiken": {"type": "array", "maxItems": "14", "items": {"type": "object", "properties": {
            "art": {"type": "string"}, "von": {"type": "integer"}, "bis": {"type": "integer"}, "karte": {"type": "boolean"},
            "kicker": {"type": "string"}, "gross": {"type": "string"}, "akzent": {"type": "string"},
            "zahl": {"type": "string"}, "zahl2": {"type": "string"}, "einheit": {"type": "string"},
            "texte": {"type": "array", "items": {"type": "object", "properties": {
                "text": {"type": "string"}, "sub": {"type": "string"}, "icon": {"type": "string"}, "wort": {"type": "integer"}, "ok": {"type": "boolean"}},
                "required": ["text"]}}},
            "required": ["art", "von", "bis"]}},
        "schluss": {"type": "object", "properties": {"zeile": {"type": "string"}, "wort": {"type": "string"}}}},
        "required": ["hook", "betont", "grafiken"]}
    system += (" Wichtig: Decke das GANZE Video ab, auch die zweite Hälfte. Listenpunkte ('haken') sind vollständige Aussagen "
               "(„Keine Startkosten“, nicht „Startkosten“). hook.gross nennt das Thema in einem allgemein verständlichen Wort, "
               "kein Fachkürzel.")
    dur = T[-1][2]
    soll = max(4, int(dur / 8))
    user = f"Titel: {title or '-'}\nLänge: {dur:.0f} Sekunden – plane mindestens {soll} Grafiken.\n\nText:\n{text}"
    res = ki_call(system, user, schema)
    if not res:
        log("Premium-Grafiken: keine KI-Antwort")
        return None
    _zahlen(res)
    _nur_gesagtes(res, T, log)
    gut = [g for g in res.get("grafiken") or [] if g.get("art") in erlaubt]
    gross_da = any(g.get("art") in ("voll_wort", "voll_zahl") or g.get("karte") for g in gut)
    zeiten = sorted(T[g["von"]][1] for g in gut if isinstance(g.get("von"), int) and 0 <= g["von"] < len(T))
    loch = max([b - a for a, b in zip([0.0] + zeiten, zeiten + [dur])] or [dur])
    if len(gut) < soll or not gross_da or loch > 14:   # zu dünn, nur kleine Karten oder lange Strecke ohne Grafik → einmal nachfordern
        res2 = ki_call(system, user + f"\n\nDein erster Plan war zu dünn ({len(gut)} Grafiken, "
                       f"{'mit' if gross_da else 'ohne'} großes Element, längste Strecke ohne Grafik {loch:.0f} s): {json.dumps(res.get('grafiken'), ensure_ascii=False)[:1500]}\n"
                       f"Liefere einen vollständigen neuen Plan mit mindestens {soll} Grafiken über das ganze Video, davon mindestens "
                       "eine Vollbild-Karte oder eine Grafik mit 'karte': true.", schema)
        _zahlen(res2)
        _nur_gesagtes(res2, T, log)
        if res2 and len([g for g in res2.get("grafiken") or [] if g.get("art") in erlaubt]) >= len(gut):
            res = res2
    res["_erlaubt"] = erlaubt
    return res


def ereignisse(plan, T, dur, seed, log=print):
    """KI-Plan → geprüfte Ereignisse für den Baukasten (Zeiten in Sekunden des fertigen Videos)."""
    rnd = random.Random(seed + 1)
    n = len(T)
    t = lambda i, off=0.0: round(max(0.0, min(dur, T[max(0, min(n - 1, int(i)))][1] + off)), 2)
    te = lambda i: T[max(0, min(n - 1, int(i)))][2]
    ev, used, papier = [], {}, rnd.random() < 0.5
    icon = lambda x: x if x in ICONS else "star"

    def items(g, a, b, mx):
        out = []
        tx = [x for x in (g.get("texte") or []) if _kurz(x.get("text"), 26)][:mx]
        for k, x in enumerate(tx):
            w = x.get("wort")
            ti = t(w, -0.1) if isinstance(w, int) and a - 0.3 <= t(w) <= b else round(a + (k + 0.4) * (b - a) / (len(tx) + 0.5), 2)
            out.append((x, max(a, ti)))
        return out

    def add(g, hook=False):
        nonlocal papier
        art = g.get("art")
        if art not in ARTEN or used.get(art, 0) >= 2:
            return
        von, bis = g.get("von", 0), g.get("bis", 0)
        if not (isinstance(von, int) and isinstance(bis, int)) or bis < von:
            return
        a = 0.15 if hook else t(von, -0.15)
        b = min(dur - 0.3, max(te(bis) + 0.5, a + 1.4))
        b = min(b, a + (3.2 if art in ("voll_wort", "voll_zahl", "dahinter", "wort") else 8.0))
        G, K, AK = _kurz(g.get("gross"), 24), _kurz(g.get("kicker"), 28), _kurz(g.get("akzent"), 22)
        e = None
        if art == "chips":
            it = items(g, a, b, 2)
            if it:
                e = dict(type="chips", items=[dict(icon=icon(x.get("icon")), text=x["text"], a=ti, **({"sub": _kurz(x.get("sub"), 24)} if _kurz(x.get("sub"), 24) else {}),
                                                  **({"state": "ok" if x["ok"] else "no"} if isinstance(x.get("ok"), bool) else {})) for x, ti in it])
        elif art == "zahl" and isinstance(g.get("zahl"), (int, float)) and g["zahl"] > 0:
            e = dict(type="counter", to=g["zahl"], label=G or "", sub=K or "", suffix=_kurz(g.get("einheit"), 6) and " " + g["einheit"].strip(), dur=0.6,
                     **({"to2": g["zahl2"]} if isinstance(g.get("zahl2"), (int, float)) else {}), size=84 if g.get("zahl2") or g["zahl"] >= 10000 else 96)
        elif art == "schritte":
            it = items(g, a, b, 5)
            if len(it) >= 2:
                e = dict(type="steps", header=K or "Ablauf", idle=G or "", items=[dict(a=ti, text=x["text"]) for x, ti in it])
        elif art == "haken":
            it = items(g, a, b, 3)
            if it:
                e = dict(type="checks", items=[dict(a=ti, text=x["text"], **({"ok": False} if x.get("ok") is False else {})) for x, ti in it])
        elif art == "trichter":
            it = items(g, a, b, 3)
            if len(it) >= 2:
                e = dict(type="funnel", rows=[dict(a=ti, label=x["text"], value=_kurz(x.get("sub"), 10) or "", w=[1.0, 0.66, 0.34][k]) for k, (x, ti) in enumerate(it)])
        elif art == "nummer" and isinstance(g.get("zahl"), (int, float)) and G:
            e = dict(type="num", n=str(int(g["zahl"])), kicker=K or "", text=G, **({"sub": AK} if AK else {}))
        elif art == "wort" and G and len(G) <= 16:
            e = dict(type="big", text=G, **({"kicker": K} if K else {}), size=118 if len(G) <= 11 else 96)
        elif art == "voll_wort" and G:
            z = [x.strip().upper() for x in re.split(r"\\n|\n", G) if x.strip()][:2]
            if z and max(len(x) for x in z) <= 12:
                papier = not papier
                e = dict(type="full", big="\n".join(z), bsize=230 if max(len(x) for x in z) <= 9 else 180, **({"kicker": K.upper()} if K else {}),
                         **({"accent": AK} if AK and len(AK) <= 14 else {}), **({"bg": "paper"} if papier else {}))
        elif art == "voll_zahl" and isinstance(g.get("zahl"), (int, float)) and g["zahl"] > 0:
            papier = not papier
            two = isinstance(g.get("zahl2"), (int, float))
            e = dict(type="full", num=dict(to=g["zahl"], **({"to2": g["zahl2"]} if two else {}), **({"suffix": " " + g["einheit"].strip()} if _kurz(g.get("einheit"), 4) else {})),
                     size=215 if two or g["zahl"] >= 1000 else 300, dur=0.6, **({"kicker": K.upper()} if K else {}), **({"sub": G} if G else {}), **({"bg": "paper"} if papier else {}))
        elif art == "chat":
            ln = [x["text"] for x, _ in items(g, a, b, 2) if len(x["text"]) <= 22]
            if ln:
                e = dict(type="chat", lines=ln)
        elif art == "suche" and G:
            e = dict(type="search", text=G)
        elif art == "anruf" and G:
            e = dict(type="call", name=G, **({"sub": AK} if AK else {}))
        elif art == "dahinter" and G and len(G) <= 11 and used.get("dahinter", 0) < 1:
            e = dict(type="behind", text=G.upper(), **({"gold": True} if rnd.random() < 0.5 else {}))
        if not e:
            return
        if g.get("karte") and e["type"] in ("steps", "checks", "funnel") and sum(1 for x in ev if x.get("frame")) < 2:
            e.update(frame=True, pscale={"steps": 1.15, "checks": 1.45, "funnel": 1.3}[e["type"]])
        e.update(a=round(a, 2), b=round(b, 2))
        used[art] = used.get(art, 0) + 1
        ev.append(e)

    h = plan.get("hook") or {}
    if h.get("gross"):
        add({"art": h.get("art") if h.get("art") in ("dahinter", "voll_wort", "wort") else "wort", "gross": h["gross"], "kicker": h.get("klein"), "von": 0, "bis": h.get("bis", 6)}, hook=True)
    for g in sorted(plan.get("grafiken") or [], key=lambda x: x.get("von", 0) if isinstance(x.get("von"), int) else 0):
        add(g)
    # Überschneidungen: Vollbild-Karten liegen über allem, alles andere teilt sich die Bühne
    ev.sort(key=lambda e: e["a"])
    stage = [e for e in ev if e["type"] != "full"]
    for x, y in zip(stage, stage[1:]):
        if y["a"] < x["b"] + 0.05:
            x["b"] = round(y["a"] - 0.05, 2)
    full = [e for e in ev if e["type"] == "full"]
    for f in full:   # Bühnen-Grafik unter einer Vollbild-Karte: weglassen, wenn sie darin fast ganz verschwindet
        stage = [e for e in stage if not (e["a"] >= f["a"] - 0.2 and e["b"] <= f["b"] + 0.2)]
    ev = [e for e in stage + full if e["b"] - e["a"] >= 1.0]
    for e in ev:  # Unterpunkte innerhalb der Grafik halten
        for k in ("items", "rows"):
            for it in e.get(k, []):
                it["a"] = round(min(max(it["a"], e["a"]), e["b"] - 0.5), 2)
    return sorted(ev, key=lambda e: e["a"])


def untertitel(T, betont):
    out, cur, chars = [], [], 0
    for i, (w, s, _) in enumerate(T):
        e = i in betont and len(norm(w)) > 2
        L = len(w) * (1.3 if e else 1)
        gap = s - T[i - 1][1] if i else 0
        if cur and (len(cur) >= 3 or chars + L > 19 or (e and len(w) > 11) or gap > 1.2):
            out.append(cur); cur = []; chars = 0
        cur.append([w, round(s, 2), bool(e)]); chars += L + 1
        if re.search(r"[.,!?:]$", w) or (e and len(w) > 11):
            out.append(cur); cur = []; chars = 0
    if cur:
        out.append(cur)
    return out


def bildlage(video):
    """Gesicht suchen → Bild so legen, dass der Haaransatz bei ~715 px liegt und über dem Kopf Platz für Grafik ist."""
    import cv2, numpy as np
    cc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    cap = cv2.VideoCapture(video)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); fb = []
    for f in range(10, max(11, n), max(15, n // 40)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, img = cap.read()
        if not ok:
            break
        g = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        fs = cc.detectMultiScale(g, 1.1, 5, minSize=(200, 200))
        if len(fs):
            fb.append(max(fs, key=lambda b: b[2] * b[3]))
    if len(fb) < 3:
        return dict(s=1.0, tx=0, ty=0, ox=540, oy=1150), False
    x, y, w, h = np.median(np.array(fb), axis=0)
    cx, cy = x + w / 2, y + h / 2
    sc = 1.0 if w >= 450 else min(1.45, 560 / w)
    hair = cy + ((y - 0.2 * h) - cy) * sc
    ty = 715 - hair
    ty = max(0, min(260, ty)) if sc == 1 else max(-(sc - 1) * 960 * 0.9, min(260, ty))
    tx = 0 if sc == 1 else max(-(sc - 1) * 540 * 0.9, min((sc - 1) * 540 * 0.9, 540 - cx))
    return dict(s=round(sc, 3), tx=round(tx), ty=round(ty), ox=round(cx), oy=round(cy)), True


def _projekt():
    """Baukasten auf den Mac spiegeln (auf der SSD kann nicht gebaut werden) und Maskier-Werkzeug bereitstellen."""
    os.makedirs(CACHE, exist_ok=True)
    subprocess.run(["rsync", "-a", "--delete", "--exclude", "node_modules", "--exclude", "public/v", "--exclude", "matte", "--exclude", "._*", PROJ + "/", CACHE + "/"], check=True)
    nm = os.path.join(CACHE, "node_modules")
    if not os.path.exists(nm):
        os.symlink(MODULES, nm)
    src = os.path.join(CACHE, "matte.swift")
    if os.path.exists(src) and (not os.path.exists(MATTE) or os.path.getmtime(MATTE) < os.path.getmtime(src)):
        subprocess.run(["swiftc", "-O", src, "-o", MATTE], check=True, capture_output=True)
    os.makedirs(os.path.join(CACHE, "public", "v"), exist_ok=True)


def render(cut, words_out, title, out, ki_call, cut_times=(), logo=None, accent=None, seed_text="", log=print, plan=None, brand=None, outro=True,
           brolls=(), bilder=()):
    """brolls: [{"file", "t", "dur"}] – Zwischenschnitte des Kunden (Zeit im fertigen Clip); bilder: [{"file", "t"}]."""
    """Premium-Video bauen. Rückgabe: Pfad der fertigen Datei oder None (dann zeichnet die Pipeline wie bisher)."""
    T = []
    for w, s, e in words_out:
        if w.startswith("-") and T:
            T[-1][0] += w
        else:
            T.append([w, float(s), float(e)])
    T = [tuple(x) for x in T]
    if len(T) < 6:
        return None
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", cut], capture_output=True, text=True).stdout or 0)
    if dur < 5 or dur > 180:
        return None
    seed = int(hashlib.md5((seed_text or title or T[0][0]).encode()).hexdigest()[:8], 16)
    plan = plan or plane(T, ki_call, title, seed, log)
    if not plan:
        return None
    ev = ereignisse(plan, T, dur, seed, log)
    if len(ev) < 2:
        log("Premium-Grafiken: zu wenig Brauchbares im Plan")
        return None
    _projekt()
    key = hashlib.md5(os.path.abspath(out).encode()).hexdigest()[:10]
    pub = os.path.join(CACHE, "public", "v")
    vid = f"v/{key}.mp4"
    shutil.copyfile(cut, os.path.join(CACHE, "public", vid))
    base, face = bildlage(cut)
    # B-Roll und Bilder des Kunden: abwechselnd als Vollbild-Zwischenschnitt, als Karte unter dem Sprecher oder im Handy-Mockup
    rnd = random.Random(seed + 2)
    arten = rnd.sample(["broll", "media", "phone"], 3)
    med = []
    for k, b in enumerate(sorted(list(brolls) + [dict(x, img=True, dur=2.4) for x in bilder], key=lambda x: x["t"])):
        if not os.path.exists(b["file"]) or b["t"] > dur - 1.2 or (med and b["t"] - med[-1]["b"] < 1.5):
            continue
        ext = os.path.splitext(b["file"])[1].lower()
        dst = f"v/{key}_b{k}{ext}"
        shutil.copyfile(b["file"], os.path.join(CACHE, "public", dst))
        art = "media" if b.get("img") else arten[len(med) % 3]
        if art == "media" and not face:
            art = "broll"
        d = min(float(b.get("dur") or 2.4), 3.2 if art != "media" else 4.0, dur - b["t"] - 0.3)
        e = dict(type=art, a=round(b["t"], 2), b=round(b["t"] + max(1.2, d), 2), src=dst, **({"img": True} if b.get("img") else {}))
        if art == "media":
            e.update(frame=True, pscale=1.3)
        med.append(e)
    if med:
        # eigenes Material des Kunden hat Vorrang: KI-Grafiken, die sich damit überschneiden, weichen
        keep = []
        for e in ev:
            hit = [m for m in med if min(e["b"], m["b"]) - max(e["a"], m["a"]) > 0.2]
            if not hit:
                keep.append(e)
            elif e["type"] in ("full", "behind") or any(m["type"] == "media" for m in hit) or e.get("frame"):
                continue
            else:
                keep.append(e)   # kleine Bühnen-Grafik unter einem Vollbild-Zwischenschnitt: bleibt (ist dann kurz verdeckt)
        ev = sorted(keep + med, key=lambda e: e["a"])
    if not face:   # ohne Gesicht kein „dahinter“ und keine Sprecher-Karte
        ev = [e for e in ev if e["type"] != "behind"]
        for e in ev:
            e.pop("frame", None); e.pop("pscale", None)
    # Text hinter der Person: Maske je Bild → Video mit Transparenz
    for i, e in enumerate(x for x in ev if x["type"] == "behind"):
        try:
            f0 = max(0, math.floor((e["a"] - 0.1) * 30)); n = math.ceil((e["b"] + 0.1) * 30) - f0
            md = os.path.join(pub, f"{key}_m{i}"); shutil.rmtree(md, ignore_errors=True)
            subprocess.run([MATTE, cut, md, f"{f0 / 30 - 0.004:.4f}", f"{(f0 + n) / 30 + 0.2:.4f}"], check=True, capture_output=True)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{f0 / 30:.5f}", "-i", cut, "-framerate", "30", "-i", f"{md}/%05d.png", "-filter_complex",
                            "[1:v]format=gray,gblur=sigma=1.2[m];[0:v]fps=30[v];[v][m]alphamerge,format=yuva420p[o]", "-map", "[o]", "-frames:v", str(n), "-an",
                            "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "22", "-row-mt", "1", "-cpu-used", "4", os.path.join(pub, f"{key}_bh{i}.webm")], check=True)
            e.update(cut=f"v/{key}_bh{i}.webm", cf=f0, cn=n); shutil.rmtree(md, ignore_errors=True)
        except Exception as ex:  # noqa: BLE001
            log(f"Text hinter der Person nicht möglich ({ex}) – ohne")
            e["type"] = "big"; e["size"] = 110
    # Kamera: an Schnitten zwischen zwei Zoom-Stufen wechseln (weich), frühestens alle 2,2 s
    cam, last, hi = [[0, 1.0, 1.03]], 0.0, False
    for c in sorted(cut_times):
        if c - last >= 2.2 and dur - c > 1.2:
            hi = not hi; cam.append([round(c, 3), 1.07, 1.09] if hi else [round(c, 3), 1.0, 1.03]); last = c
    sweeps = [c[0] for c in cam[1:]][2::5][:3]
    sfx = []
    for e in ev:
        ty = e["type"]
        if ty in ("big", "behind"): sfx.append([e["a"], "wh", .3])
        elif ty == "full":
            sfx.append([e["a"], "wh", .3])
            if e.get("num"): sfx.append([e["a"] + 0.15 + e.get("dur", .7), "po", .34])
        elif ty == "num": sfx += [[e["a"], "wh", .2], [e["a"] + 0.1, "po", .3]]
        elif ty in ("chips", "steps", "checks"): sfx += [[i["a"], "po", .28] for i in e["items"]]
        elif ty == "funnel": sfx += [[i["a"], "po", .28] for i in e["rows"]]
        elif ty == "counter": sfx.append([e["a"] + 0.65, "po", .34])
        elif ty in ("chat", "search", "call"): sfx.append([e["a"] + 0.1, "po", .3])
        elif ty in ("broll", "phone", "media"): sfx.append([e["a"], "wh", .22])
        if e.get("frame"): sfx.append([max(0, e["a"] - 0.2), "wh", .2])
    sfx += [[c, "wh", .14] for c in sweeps]
    sch = plan.get("schluss") or {}
    props = dict(file=vid, dur=round(dur, 3), base=base, cam=cam, sweeps=sweeps, chunks=untertitel(T, set(i for i in plan.get("betont") or [] if isinstance(i, int))),
                 events=ev, sfx=[dict(t=round(a, 2), k=k, vol=v) for a, k, v in sfx],
                 outro=(dict(line=(_kurz(sch.get("zeile"), 22) if _kurz(sch.get("wort"), 14) else "") or "Folg für", gold=(_kurz(sch.get("wort"), 14) if _kurz(sch.get("zeile"), 22) else "") or "mehr", handle="", site="") if outro else None),
                 brand=brand or ({"logo": None} if not logo else {"logo": f"v/{key}_logo.png"}))
    if logo and not brand:
        shutil.copyfile(logo, os.path.join(pub, f"{key}_logo.png"))
    if outro: sfx.append([dur - 0.1, "wh", .32]); props["sfx"].append(dict(t=round(dur - 0.1, 2), k="wh", vol=.32))
    if accent and re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
        r_, g_, b_ = (int(accent[i:i + 2], 16) for i in (1, 3, 5))
        props["theme"] = dict(accent=accent, accentL="#%02x%02x%02x" % tuple(int(c + (255 - c) * 0.45) for c in (r_, g_, b_)))
    pf = os.path.join(CACHE, f"props_{key}.json")
    json.dump(props, open(pf, "w"), ensure_ascii=False)
    tmp = os.path.join(CACHE, f"out_{key}.mp4")
    r = subprocess.run(["npx", "remotion", "render", "src/index.ts", "Batch", tmp, f"--props={pf}", "--public-dir=public", "--crf=17", "--concurrency=3", "--log=error"],
                       cwd=CACHE, capture_output=True, text=True)
    for f in [os.path.join(CACHE, "public", vid)] + [os.path.join(pub, x) for x in os.listdir(pub) if x.startswith(key)]:
        try: os.remove(f)
        except OSError: pass
    if r.returncode != 0 or not os.path.exists(tmp):
        log("Premium-Rendern fehlgeschlagen: " + (r.stderr or r.stdout)[-300:])
        return None
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", tmp, "-c:v", "copy", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                    "-movflags", "+faststart", out], check=True)
    os.remove(tmp)
    log(f"Premium-Grafiken: {len(ev)} Elemente – " + ", ".join(e["type"] + ("/Karte" if e.get("frame") else "") for e in ev))
    return out, dict(elemente=[e["type"] for e in ev], erlaubt=plan.get("_erlaubt"), props=pf, abspann_s=2.6 if outro else 0.0,
                     untertitel=sum(len(c) for c in props["chunks"]))
