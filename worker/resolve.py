"""Velox Cut – Anschluss an DaVinci Resolve (kostenlose Ausgabe 21.1).

Resolve läuft als Farb-Stufe und als Schnittplatz der Schnitt-Maschine:
  • grade()    – fertig geschnittener Clip → Resolve → Farbkorrektur (Schwarz-/Weißpunkt, Weißabgleich, Mitten,
                 Sättigung als CDL auf Node 1) → gerendert zurück. Der Ton des Clips bleibt unangetastet.
  • timeline() – Rohvideo + Schnittliste der Pipeline → bearbeitbare Timeline in Resolve (von Hand weiterschneiden).

Weg zu Resolve: Die kostenlose Ausgabe nimmt seit 21.1 keine Python-Skripte mehr an. Offen ist nur das Lua-Skript
„resolve_mcp_bridge“ (Workspace → Scripts, nach JEDEM Resolve-Start einmal anklicken). Mit ihm spricht der kleine
Node-Server der Claude-Erweiterung; den starten wir hier selbst und schicken ihm Lua.

Alles hier ist freiwillig: Läuft Resolve nicht oder geht etwas schief, wirft das Modul ResolveFehler und die Pipeline
arbeitet wie bisher weiter (ffmpeg-Farbe). Ein Kundenauftrag scheitert nie an Resolve.

Aufruf von Hand:
  python resolve.py status
  python resolve.py grade <clip.mp4> <ziel.mp4> [--style clean|viral|premium] [--behalten]
  python resolve.py timeline <roh.mp4> <schnittliste.json> [--name NAME]
Umgebung: VELOX_RESOLVE_SERVER (index.js der Brücke), VELOX_NODE (node), VELOX_RESOLVE_BEHALTEN=1 (Timeline bleibt stehen).
"""
import json, os, queue, shutil, subprocess, sys, threading, time, uuid

SERVER = os.environ.get("VELOX_RESOLVE_SERVER", os.path.expanduser(
    "~/Library/Application Support/Claude/Claude Extensions/local.mcpb.saad-khan.davinci-resolve-lua-mcp/server/index.js"))
NODE = os.environ.get("VELOX_NODE") or shutil.which("node") or "/usr/local/bin/node"
BIN = "Velox Maschine"       # Ordner im Medienpool für die automatischen Durchläufe
BIN_HAND = "Velox Aufträge"  # Ordner für Timelines zum Weiterschneiden
RATEN = (23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 59.94, 60.0)  # Bildraten, die Resolve als Timeline annimmt


class ResolveFehler(RuntimeError):
    pass


def q(s):
    """Text als Lua-Zeichenkette (Pfade mit Anführungszeichen, Leerzeichen, Umlauten)."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "") + '"'


def rate(fps):
    """Passende Timeline-Bildrate als Text – oder None, wenn Resolve diese Rate nicht kennt."""
    for r in RATEN:
        if abs(fps - r) < 0.011:
            return f"{r:g}"
    return None


class Bruecke:
    """Eine Verbindung zum Brücken-Server (JSON-RPC über stdin/stdout). Immer mit „with“ benutzen."""

    def __init__(self):
        if not os.path.exists(SERVER):
            raise ResolveFehler("Brücken-Server nicht gefunden")
        self.p = subprocess.Popen([NODE, SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, bufsize=1)
        self.q, self.n = queue.Queue(), 0
        threading.Thread(target=self._lesen, daemon=True).start()
        self._ruf("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "velox-schnitt-maschine", "version": "1"}}, 15)
        self._senden({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def __enter__(self):
        return self

    def __exit__(self, *a):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=3)
        except Exception:
            self.p.kill()

    def _lesen(self):
        for zeile in self.p.stdout:
            try:
                self.q.put(json.loads(zeile))
            except ValueError:
                pass
        self.q.put(None)

    def _senden(self, m):
        try:
            self.p.stdin.write(json.dumps(m) + "\n")
            self.p.stdin.flush()
        except (BrokenPipeError, OSError):
            raise ResolveFehler("Brücken-Server beendet")

    def _ruf(self, method, params, timeout):
        self.n += 1
        self._senden({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params})
        ende = time.time() + timeout
        while True:
            try:
                m = self.q.get(timeout=max(0.1, ende - time.time()))
            except queue.Empty:
                raise ResolveFehler(f"keine Antwort von Resolve ({method})")
            if m is None:
                raise ResolveFehler("Brücken-Server beendet")
            if m.get("id") == self.n:
                if "error" in m:
                    raise ResolveFehler(str(m["error"].get("message", m["error"]))[:200])
                return m.get("result") or {}

    def werkzeug(self, name, args=None, timeout=40):
        r = self._ruf("tools/call", {"name": name, "arguments": args or {}}, timeout)
        text = "".join(c.get("text", "") for c in r.get("content", []) if c.get("type") == "text")
        try:
            d = json.loads(text)
        except ValueError:
            raise ResolveFehler(text[:200] or "leere Antwort")
        if r.get("isError"):
            raise ResolveFehler(str(d.get("error") or d.get("message") or text)[:200])
        return d

    def lua(self, code, timeout=30):
        d = self.werkzeug("run_lua", {"code": code, "timeout_s": int(timeout)}, timeout + 10)
        if not d.get("ok"):
            raise ResolveFehler(str(d.get("error") or d)[:300])
        return d.get("result")


def status():
    """{'alive': bool, 'project': …, 'version': …} – wirft nie."""
    try:
        with Bruecke() as b:
            s = b.werkzeug("resolve_status", timeout=20)
            return {"alive": bool(s.get("alive")), "project": s.get("project"), "version": s.get("version"),
                    "grund": s.get("reason") or s.get("why")}
    except Exception as e:
        return {"alive": False, "grund": str(e)}


def alive():
    s = status()
    return bool(s["alive"] and s.get("project"))


# ───────────────────────────── Farbkorrektur messen ─────────────────────────────
def auto_cdl(path, style="clean", spans=None):
    """Misst den (ungegradeten) Clip an 12 Bildern und liefert die Korrektur als CDL: out = (in·Slope + Offset)^Power.
    Vorsichtig wie ein Colorist bei der Grundkorrektur – jeder Wert ist begrenzt, damit nie ein Video „kaputtkorrigiert“ wird:
      Schwarzpunkt/Weißpunkt  → Slope + Offset   (flaues Handybild bekommt Kontrast, nichts läuft zu oder brennt aus)
      Mitten                  → Power            (dunkle Videos heller, zu helle etwas dichter)
      Weißabgleich            → Slope je Kanal   (nur aus fast farblosen hellen Flächen, halbe Stärke, höchstens 5 %)
      Sättigung               → Saturation       (blasse Videos mehr, schon bunte kaum)
    spans = [(bild_von, bild_bis), …] misst nur diese Stücke (eine Aufnahme innerhalb eines Videos aus mehreren Clips)."""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    spans = [(a, e) for a, e in (spans or [(0, n)]) if e > a]
    lang = sum(e - a for a, e in spans) or 1
    stellen = []
    for i in range(12):  # 12 Bilder gleichmäßig über die Stücke verteilt
        pos = lang * (i + 0.5) / 12
        for a, e in spans:
            if pos < e - a:
                stellen.append(int(a + pos))
                break
            pos -= e - a
    bilder = []
    for st in sorted(set(stellen)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, st)
        ok, f = cap.read()
        if ok:
            bilder.append(cv2.resize(f, (270, 480), interpolation=cv2.INTER_AREA))
    cap.release()
    if not bilder:
        raise ResolveFehler("Clip nicht lesbar")
    px = np.concatenate([b.reshape(-1, 3) for b in bilder]).astype(np.float32) / 255.0
    b_, g_, r_ = px[:, 0], px[:, 1], px[:, 2]
    y = 0.2126 * r_ + 0.7152 * g_ + 0.0722 * b_
    p1, p99 = (float(v) for v in np.percentile(y, [1, 99]))
    # Mitten: am GESICHT messen, wenn eins zu sehen ist – das ist das, worauf der Zuschauer schaut. Testlauf 12.10. (Cheesecake):
    # die Bildmitte war ein schwarzes Hemd, das Bild galt als „dunkel“ und wurde zu stark aufgehellt. Haut darf je nach Mensch
    # und Licht zwischen 0,40 und 0,60 liegen; nur was außerhalb liegt, wird (zu 70 %) an den Rand herangeführt.
    # Ohne Gesicht (Essen, Hände, Totale): ganzes Bild, nur sanft Richtung 0,43.
    haut = []
    try:
        kas = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        for b in bilder:
            fs = kas.detectMultiScale(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), 1.12, 5, minSize=(26, 26))
            if len(fs):
                x, y0, w, h = max(fs, key=lambda q_: q_[2] * q_[3])
                g = b[y0 + h // 4:y0 + 3 * h // 4, x + w // 4:x + 3 * w // 4].reshape(-1, 3).astype(np.float32) / 255.0
                haut.append(float(np.median(0.2126 * g[:, 2] + 0.7152 * g[:, 1] + 0.0722 * g[:, 0])))
    except Exception:
        haut = []
    gesicht = len(haut) >= max(2, len(bilder) // 4)
    p50 = float(np.median(haut)) if gesicht else float(np.median(y))
    p1, p99 = (float(v) for v in np.percentile(y, [1, 99]))
    # Schwarz- und Weißpunkt: Ziel 0,03 … 0,94
    # Nur 60 % des Weges und höchstens +18 %: Ein bewusst dunkel/stimmungsvoll gedrehtes Bild (Restaurant am Abend, Weiß bei
    # 0,69) soll frischer werden, aber nicht taghell – Test Golden Thai 12.10.: volle Streckung (×1,30) ließ Tisch und Haut glühen.
    s = min(1.18, max(0.96, 1 + 0.6 * ((0.94 - 0.03) / max(0.25, p99 - p1) - 1)))
    o = min(0.05, max(-0.05, 0.03 - p1 * s))
    if p99 * s + o > 0.97:  # Lichter nicht ausbrennen lassen
        s = max(0.94, (0.97 - o) / max(p99, 0.3))
    m = min(0.95, max(0.02, p50 * s + o))
    if gesicht:
        ziel = m + 0.7 * (min(0.60, max(0.40, m)) - m)
        p = min(1.10, max(0.72, float(np.log(ziel) / np.log(m)))) if 0.03 < m < 0.94 else 1.0
    else:
        ziel = m + 0.3 * (0.43 - m)
        p = min(1.05, max(0.85, float(np.log(ziel) / np.log(m)))) if 0.03 < m < 0.94 else 1.0
    # Weißabgleich aus fast farblosen, mittelhellen bis hellen Flächen
    mx, mn = px.max(axis=1), px.min(axis=1)
    neutral = (y > 0.45) & (y < 0.95) & ((mx - mn) < 0.09)
    gr = gb = 1.0
    if neutral.mean() > 0.05:  # genug farblose Fläche, sonst ist die „Messung“ nur die Lichtstimmung des Raums
        R, G, B = float(r_[neutral].mean()), float(g_[neutral].mean()), float(b_[neutral].mean())
        gr = min(1.04, max(0.96, 1 + 0.5 * (G / R - 1)))
        gb = min(1.04, max(0.96, 1 + 0.5 * (G / B - 1)))
    # Sättigung
    chroma = float(((mx - mn) / np.maximum(mx, 0.05)).mean())
    sat = min(1.22, max(1.0, 1.0 + (0.34 - chroma) * 0.9))
    sr, sg, sb = s * gr, s * 1.0, s * gb
    orr, og, ob = o, o, o
    if style == "viral":       # kräftiger: mehr Kontrast in den Mitten, mehr Farbe
        sat = min(1.28, sat + 0.05)
        p = min(1.08, p + 0.02)
    elif style == "premium":   # Kino-Look: Lichter warm, Tiefen kühl, etwas dichter
        sr, sb = sr * 1.02, sb * 0.98
        orr, ob = orr - 0.006, ob + 0.006
        p = min(1.09, p + 0.03)
    return {"Slope": f"{sr:.4f} {sg:.4f} {sb:.4f}", "Offset": f"{orr:.4f} {og:.4f} {ob:.4f}",
            "Power": f"{p:.4f} {p:.4f} {p:.4f}", "Saturation": f"{sat:.3f}",
            "mess": {"p1": round(p1, 3), "p50": round(p50, 3), "p99": round(p99, 3), "chroma": round(chroma, 3),
                     "neutral": round(float(neutral.mean()), 3), "wb": [round(gr, 3), round(gb, 3)], "gesicht": gesicht}}


# ───────────────────────────── Lua-Bausteine ─────────────────────────────
_KOPF = """
local p = resolve:GetProjectManager():GetCurrentProject()
if not p then error("kein Projekt offen") end
local mp = p:GetMediaPool()
local root = mp:GetRootFolder()
local function ordner(name)
  for _, f in ipairs(root:GetSubFolderList() or {}) do if f:GetName() == name then return f end end
  return mp:AddSubFolder(root, name)
end
local function timeline(name)
  for i = 1, p:GetTimelineCount() do local t = p:GetTimelineByIndex(i); if t and t:GetName() == name then return t end end
end
"""


def _probe(path):
    d = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                                   "stream=width,height,r_frame_rate,nb_read_packets:format=duration", "-of", "json", path],
                                  capture_output=True, text=True, check=True).stdout)
    s = d["streams"][0]
    a, b = s["r_frame_rate"].split("/")
    return int(s["width"]), int(s["height"]), float(a) / float(b or 1), int(s.get("nb_read_packets") or 0), float(d["format"]["duration"])


def grade(src, dst, style="clean", cdl=None, behalten=None, melden=None, shots=None, name=None):
    """Clip `src` (fertig geschnitten, 9:16, OHNE Farbkorrektur) durch Resolve schicken → `dst` (Bild aus Resolve, Ton aus src).
    shots = [(bild_von, bild_bis, aufnahme), …] (lückenlos von 0 bis zum Ende): Video aus mehreren Rohclips – jede Aufnahme
    bekommt ihre eigene, an ihr gemessene Korrektur (dieselbe Aufnahme immer dieselbe), damit hell und dunkel, warm und kalt
    gedrehte Clips im fertigen Video zusammenpassen. Ohne shots: eine Korrektur für das ganze Video.
    Gibt die benutzte Korrektur zurück. Wirft ResolveFehler, wenn Resolve nicht kann – dann ist dst nicht angelegt."""
    behalten = os.environ.get("VELOX_RESOLVE_BEHALTEN", "0") == "1" if behalten is None else behalten
    src, dst = os.path.abspath(src), os.path.abspath(dst)
    w, h, fps, frames, dur = _probe(src)
    r = rate(fps)
    if not r:
        raise ResolveFehler(f"Bildrate {fps:.2f} kennt Resolve nicht")
    if shots and not cdl:
        shots = [(int(a), int(e), k) for a, e, k in shots if e > a]
        if not shots or shots[0][0] != 0 or shots[-1][1] != frames or any(shots[i][1] != shots[i + 1][0] for i in range(len(shots) - 1)):
            shots = [(0, frames, 0)]  # Liste passt nicht zur Bildzahl → lieber eine Korrektur für alles als gar keine
        je = {}
        for k in dict.fromkeys(k for _, _, k in shots):
            je[k] = auto_cdl(src, style, [(a, e) for a, e, kk in shots if kk == k])
        # Gleich aussehende Stücke sind dieselbe Aufnahme (derselbe Rohclip, nur an anderer Stelle geschnitten): zusammen
        # messen und gleich korrigieren – sonst könnte die Farbe an einem Sprungschnitt im selben Bild leicht springen.
        gruppen = []
        for k, c in je.items():
            m = c["mess"]
            for g in gruppen:
                n = je[g[0]]["mess"]
                if (abs(m["p1"] - n["p1"]) < 0.03 and abs(m["p50"] - n["p50"]) < 0.045 and abs(m["p99"] - n["p99"]) < 0.04
                        and abs(m["chroma"] - n["chroma"]) < 0.04 and max(abs(x - y) for x, y in zip(m["wb"], n["wb"])) < 0.012):
                    g.append(k)
                    break
            else:
                gruppen.append([k])
        for g in gruppen:
            if len(g) > 1:
                gem = auto_cdl(src, style, [(a, e) for a, e, kk in shots if kk in g])
                for k in g:
                    je[k] = gem
        teile = [(a, e, je[k]) for a, e, k in shots]
        cdl = {"shots": len(shots), "gruppen": len(gruppen), "aufnahmen": {str(k): {x: v[x] for x in ("Slope", "Offset", "Power", "Saturation", "mess")} for k, v in je.items()}}
    else:
        cdl = cdl or auto_cdl(src, style)
        teile = [(0, frames, cdl)]
    lua_teile = ", ".join(f"{{mediaPoolItem = clip, startFrame = {a}, endFrame = {e}}}" for a, e, _ in teile)
    lua_cdl = "\n".join(
        f'if not added[{i + 1}]:SetCDL({{NodeIndex = "1", Slope = {q(c["Slope"])}, Offset = {q(c["Offset"])}, Power = {q(c["Power"])}, '
        f'Saturation = {q(c["Saturation"])}}}) then error("Farbkorrektur nicht gesetzt") end' for i, (_, _, c) in enumerate(teile))
    marke = "vm_" + uuid.uuid4().hex[:10]
    ziel_dir = os.path.dirname(dst)
    roh = os.path.join(ziel_dir, marke + ".mp4")
    job = None
    with Bruecke() as b:
        try:
            start = _KOPF + f"""
if p:IsRenderingInProgress() then error("Resolve rendert gerade etwas anderes") end
local vorher = p:GetCurrentTimeline()
local vorher_name = vorher and vorher:GetName() or ""
local alt = mp:GetCurrentFolder()
local bin = ordner({q(BIN)})
mp:SetCurrentFolder(bin)
local items = mp:ImportMedia({{{q(src)}}})
if not items or not items[1] then mp:SetCurrentFolder(alt); error("Import fehlgeschlagen") end
local clip = items[1]
local tl = mp:CreateEmptyTimeline({q(marke)})
if not tl then error("Timeline nicht angelegt") end
tl:SetSetting("useCustomSettings", "1")
tl:SetSetting("timelineResolutionWidth", "{w}")
tl:SetSetting("timelineResolutionHeight", "{h}")
tl:SetSetting("timelineFrameRate", {q(r)})
tl:SetSetting("timelinePlaybackFrameRate", {q(r)})
p:SetCurrentTimeline(tl)
local added = mp:AppendToTimeline({{{lua_teile}}})
mp:SetCurrentFolder(alt)
if not added or #added ~= {len(teile)} then error("Clip nicht auf der Timeline") end
{lua_cdl}
p:SetCurrentRenderFormatAndCodec("mp4", "H264")
p:SetRenderSettings({{SelectAllFrames = true, TargetDir = {q(ziel_dir)}, CustomName = {q(marke)}, UseUniqueFilenames = false,
  FormatWidth = {w}, FormatHeight = {h}, ExportVideo = true, ExportAudio = false, VideoQuality = 40000}})
local job = p:AddRenderJob()
if not job or job == "" then error("Render-Auftrag nicht angelegt") end
if not p:StartRendering({{job}}, false) then p:DeleteRenderJob(job); error("Rendern startet nicht") end
return {{job = job, frames = tl:GetEndFrame() - tl:GetStartFrame(), vorher = vorher_name}}
"""
            warten = time.time() + 150  # rendert Resolve gerade einen anderen Auftrag, kurz warten statt aufgeben
            while True:
                try:
                    job = b.lua(start, 120)
                    break
                except ResolveFehler as e:
                    if "rendert gerade" not in str(e) or time.time() > warten:
                        raise
                    time.sleep(3)
            ende = time.time() + max(180, dur * 12)
            while True:
                st = b.lua(f'return resolve:GetProjectManager():GetCurrentProject():GetRenderJobStatus({q(job["job"])})', 30)
                zustand = (st or {}).get("JobStatus", "")
                if melden:
                    melden(int((st or {}).get("CompletionPercentage") or 0))
                if zustand == "Complete":
                    break
                if zustand in ("Failed", "Cancelled"):
                    raise ResolveFehler(f"Rendern in Resolve: {zustand} {(st or {}).get('Error', '')}".strip())
                if time.time() > ende:
                    b.lua("resolve:GetProjectManager():GetCurrentProject():StopRendering()", 20)
                    raise ResolveFehler("Rendern in Resolve dauert zu lange")
                time.sleep(1.0)
        finally:  # aufräumen: Render-Auftrag weg, Timeline + Clip nur behalten, wenn gewünscht
            try:
                b.lua(_KOPF + f"""
local job = {q((job or {}).get("job", ""))}
if job ~= "" then p:DeleteRenderJob(job) end
local behalten = {"true" if behalten else "false"}
local tl = timeline({q(marke)})
local vorher = timeline({q((job or {}).get("vorher", ""))})
if vorher then p:SetCurrentTimeline(vorher) end
if behalten and tl and {q(name or "")} ~= "" then tl:SetName({q((name or "")[:80])}) end
if not behalten then
  if tl then mp:DeleteTimelines({{tl}}) end
  local bin = ordner({q(BIN)})
  for _, c in ipairs(bin:GetClipList() or {{}}) do
    if c:GetClipProperty("File Path") == {q(src)} then mp:DeleteClips({{c}}) end
  end
end
return true
""", 30)
            except ResolveFehler:
                pass
    try:
        if not os.path.exists(roh):
            raise ResolveFehler("Resolve hat keine Datei geschrieben")
        _, _, _, f2, _ = _probe(roh)
        if abs(f2 - frames) > 1:
            raise ResolveFehler(f"Bildzahl passt nicht ({f2} statt {frames})")
        m = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", roh, "-i", src, "-map", "0:v:0", "-map", "1:a:0?", "-c", "copy",
                            "-movflags", "+faststart", dst], capture_output=True, text=True)
        if m.returncode != 0:
            raise ResolveFehler("Zusammenfügen von Bild und Ton fehlgeschlagen")
    finally:
        if os.path.exists(roh):
            os.remove(roh)
    return cdl


def _drehung(path):
    """Drehhinweis im Video (Kameras/Handys speichern Hochkant-Aufnahmen oft als liegendes Bild + „rotation“)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream_side_data=rotation",
                        "-of", "default=nw=1:nk=1", path], capture_output=True, text=True).stdout.strip()
    try:
        return int(float(r.splitlines()[0])) if r else 0
    except ValueError:
        return 0


def schnitt(teile, dst, name, behalten=True, melden=None):
    """DER SCHNITT IN RESOLVE (Inhaber 17.10.: „die Videos sollen in DaVinci geschnitten und editiert werden – Claude ist Augen,
    Ohren und Kopf“). teile = [{"src", "a", "d", "zoom", "stabil", "cdl"}] in Sendereihenfolge (Sekunden im Rohclip).
    Resolve legt daraus EINE Timeline 1080×1920/30 an: jeder Teil aus dem Original-Clip geschnitten (Start-/Endbild), hochkant
    gedreht, wenn der Clip einen Drehhinweis hat, Ausschnitt/Zoom gesetzt, auf Wunsch von Resolve stabilisiert, eigene
    Farbkorrektur (CDL) – dann rendert Resolve Bild UND Ton. Rückgabe: [(start_s, ende_s)] je Teil in der fertigen Timeline."""
    dst = os.path.abspath(dst)
    infos, nach = [], []
    for i, t in enumerate(teile):
        w, h, fps, _, _ = _probe(t["src"])
        sf, ef = int(round(t["a"] * fps)), int(round((t["a"] + t["d"]) * fps))
        infos.append(f"{{mediaPoolItem = clips[{q(os.path.abspath(t['src']))}], startFrame = {sf}, endFrame = {ef}}}")
        # Drehhinweis: Resolve beachtet ihn selbst (Test 17.10.: FX3-Hochkant als 1920×1080 + rotation=90 → im Medienpool 1080×1920);
        # eine eigene Drehung legte das Bild wieder auf die Seite. Liegende Clips füllt „scaleToCrop“ (Mitte, 9:16).
        c = t.get("cdl") or {}
        cdl = (f'v:SetCDL({{NodeIndex = "1", Slope = {q(c["Slope"])}, Offset = {q(c["Offset"])}, Power = {q(c["Power"])}, Saturation = {q(c["Saturation"])}}})'
               if c else "")
        nach.append(f"""
do local v = vids[{i + 1}]
  v:SetProperty('ZoomX', {float(t.get('zoom', 1.0)):.4f}); v:SetProperty('ZoomY', {float(t.get('zoom', 1.0)):.4f})
  {"v:Stabilize()" if t.get("stabil") else ""}
  {cdl}
end""")
    pfade = list(dict.fromkeys(os.path.abspath(t["src"]) for t in teile))
    marke = "vs_" + uuid.uuid4().hex[:10]
    ziel_dir = os.path.dirname(dst)
    job = None
    with Bruecke() as b:
        start = _KOPF + f"""
if p:IsRenderingInProgress() then error("Resolve rendert gerade etwas anderes") end
local alt = mp:GetCurrentFolder()
mp:SetCurrentFolder(ordner({q(BIN)}))
local clips = {{}}
local neu = mp:ImportMedia({{{", ".join(q(x) for x in pfade)}}}) or {{}}
for _, c in ipairs(neu) do clips[c:GetClipProperty("File Path")] = c end
for _, c in ipairs(mp:GetCurrentFolder():GetClipList() or {{}}) do
  local fp = c:GetClipProperty("File Path"); if fp and not clips[fp] then clips[fp] = c end
end
for _, fp in ipairs({{{", ".join(q(x) for x in pfade)}}}) do if not clips[fp] then error("Import fehlgeschlagen: " .. fp) end end
local tl = mp:CreateEmptyTimeline({q(name[:80] if name else marke)})
if not tl then error("Timeline nicht angelegt") end
tl:SetSetting("useCustomSettings", "1")
tl:SetSetting("timelineResolutionWidth", "1080")
tl:SetSetting("timelineResolutionHeight", "1920")
tl:SetSetting("timelineFrameRate", "30")
tl:SetSetting("timelinePlaybackFrameRate", "30")
tl:SetSetting("timelineInputResMismatchBehavior", "scaleToCrop")
p:SetCurrentTimeline(tl)
local added = mp:AppendToTimeline({{{", ".join(infos)}}})
mp:SetCurrentFolder(alt)
if not added then error("Schnitt nicht auf der Timeline") end
local vids = {{}}
for _, it in ipairs(added) do local ti = it:GetTrackTypeAndIndex(); if ti and ti[1] == "video" then table.insert(vids, it) end end
if #vids ~= {len(teile)} then error("Teile auf der Timeline: " .. #vids .. " statt {len(teile)}") end
{"".join(nach)}
local t0 = tl:GetStartFrame()
local zeiten = {{}}
for _, v in ipairs(vids) do table.insert(zeiten, {{v:GetStart() - t0, v:GetEnd() - t0}}) end
p:SetCurrentRenderFormatAndCodec("mp4", "H264")
p:SetRenderSettings({{SelectAllFrames = true, TargetDir = {q(ziel_dir)}, CustomName = {q(marke)}, UseUniqueFilenames = false,
  FormatWidth = 1080, FormatHeight = 1920, ExportVideo = true, ExportAudio = true, VideoQuality = 40000}})
local job = p:AddRenderJob()
if not job or job == "" then error("Render-Auftrag nicht angelegt") end
if not p:StartRendering({{job}}, false) then p:DeleteRenderJob(job); error("Rendern startet nicht") end
return {{job = job, zeiten = zeiten, timeline = tl:GetName()}}
"""
        warten = time.time() + 150
        while True:
            try:
                job = b.lua(start, 240)
                break
            except ResolveFehler as e:
                if "rendert gerade" not in str(e) or time.time() > warten:
                    raise
                time.sleep(3)
        ende = time.time() + max(300, sum(t["d"] for t in teile) * 20)
        while True:
            st = b.lua(f'return resolve:GetProjectManager():GetCurrentProject():GetRenderJobStatus({q(job["job"])})', 30)
            zustand = (st or {}).get("JobStatus", "")
            if melden:
                melden(int((st or {}).get("CompletionPercentage") or 0))
            if zustand == "Complete":
                break
            if zustand in ("Failed", "Cancelled"):
                raise ResolveFehler(f"Rendern in Resolve: {zustand} {(st or {}).get('Error', '')}".strip())
            if time.time() > ende:
                b.lua("resolve:GetProjectManager():GetCurrentProject():StopRendering()", 20)
                raise ResolveFehler("Rendern in Resolve dauert zu lange")
            time.sleep(1.0)
        b.lua(f"local p = resolve:GetProjectManager():GetCurrentProject(); p:DeleteRenderJob({q(job['job'])}); resolve:GetProjectManager():SaveProject(); return true", 30)
    roh = os.path.join(ziel_dir, marke + ".mp4")
    if not os.path.exists(roh):
        raise ResolveFehler("Resolve hat keine Datei geschrieben")
    os.replace(roh, dst)
    return [(a / 30.0, e / 30.0) for a, e in job["zeiten"]], job.get("timeline")


def projekt(name):
    """Eigenes Resolve-Projekt je Auftrag (Inhaber 14.10.: „wenn mehrere Kunden es gleichzeitig benutzen, kannst du nicht beide
    Videos in einer Timeline schneiden“). Öffnet das Projekt `name`, legt es an, wenn es fehlt. Das bisher offene Projekt wird
    vorher gespeichert (sonst fragt Resolve nach und die Brücke hängt)."""
    with Bruecke() as b:
        return b.lua(f"""
local pm = resolve:GetProjectManager()
local cur = pm:GetCurrentProject()
if cur and cur:GetName() == {q(name)} then return {{projekt = cur:GetName(), neu = false}} end
if cur then pm:SaveProject() end
local neu = false
local p = pm:LoadProject({q(name)})
if not p then p = pm:CreateProject({q(name)}); neu = true end
if not p then error("Projekt nicht angelegt") end
return {{projekt = p:GetName(), neu = neu}}
""", 120)


def speichern():
    with Bruecke() as b:
        return b.lua("return resolve:GetProjectManager():SaveProject()", 60)


def aufraeumen(stunden=24, behalten=None):
    """Löscht Auftrags-Projekte „Velox JJJJ-MM-TT HHMM …“, die älter als `stunden` sind (Inhaber 14.10.: „behalte es maximal
    24 Stunden“). Das offene Projekt und `behalten` bleiben."""
    grenze = time.strftime("%Y-%m-%d %H%M", time.localtime(time.time() - stunden * 3600))
    with Bruecke() as b:
        return b.lua(f"""
local pm = resolve:GetProjectManager()
local cur = pm:GetCurrentProject()
local offen = cur and cur:GetName() or ""
local weg = {{}}
for _, n in ipairs(pm:GetProjectListInCurrentFolder() or {{}}) do
  local d = n:match("^Velox (%d%d%d%d%-%d%d%-%d%d %d%d%d%d) ") or n:match("^Velox (%d%d%d%d%-%d%d%-%d%d) ")
  if d and d < {q(grenze)} and n ~= offen and n ~= {q(behalten or "")} then
    if pm:DeleteProject(n) then table.insert(weg, n) end
  end
end
return weg
""", 120)


def timeline(src, ranges, name=None):
    """Rohvideo + Schnittliste [(start_s, ende_s), …] als bearbeitbare 9:16-Timeline in Resolve anlegen (Ordner „Velox Aufträge“).
    Nichts wird gerendert – das ist der Schnittplatz zum Weiterarbeiten von Hand."""
    src = os.path.abspath(src)
    w, h, fps, frames, _ = _probe(src)
    r = rate(min(fps, 60.0)) or "30"
    name = (name or os.path.splitext(os.path.basename(src))[0])[:60] + time.strftime(" %d.%m. %H-%M-%S")
    teile = ", ".join(f"{{mediaPoolItem = clip, startFrame = {max(0, round(a * fps))}, endFrame = {max(1, round(e * fps))}}}"
                      for a, e in ranges if e > a)
    if not teile:
        raise ResolveFehler("leere Schnittliste")
    with Bruecke() as b:
        return b.lua(_KOPF + f"""
local alt = mp:GetCurrentFolder()
mp:SetCurrentFolder(ordner({q(BIN_HAND)}))
local items = mp:ImportMedia({{{q(src)}}})
if not items or not items[1] then mp:SetCurrentFolder(alt); error("Import fehlgeschlagen") end
local clip = items[1]
local tl = mp:CreateEmptyTimeline({q(name)})
if not tl then error("Timeline nicht angelegt") end
tl:SetSetting("useCustomSettings", "1")
tl:SetSetting("timelineResolutionWidth", "1080")
tl:SetSetting("timelineResolutionHeight", "1920")
tl:SetSetting("timelineFrameRate", {q(r)})
tl:SetSetting("timelinePlaybackFrameRate", {q(r)})
p:SetCurrentTimeline(tl)
local added = mp:AppendToTimeline({{{teile}}})
mp:SetCurrentFolder(alt)
return {{timeline = tl:GetName(), teile = added and #added or 0}}
""", 120)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Velox Cut ↔ DaVinci Resolve")
    sub = ap.add_subparsers(dest="was", required=True)
    sub.add_parser("status")
    g = sub.add_parser("grade")
    g.add_argument("src"), g.add_argument("dst")
    g.add_argument("--style", default="clean", choices=["clean", "viral", "premium"])
    g.add_argument("--behalten", action="store_true")
    t = sub.add_parser("timeline")
    t.add_argument("src"), t.add_argument("ranges")
    t.add_argument("--name", default="")
    a = ap.parse_args()
    try:
        if a.was == "status":
            print(json.dumps(status(), ensure_ascii=False))
        elif a.was == "grade":
            print(json.dumps(grade(a.src, a.dst, a.style, behalten=a.behalten or None), ensure_ascii=False))
        else:
            d = json.load(open(a.ranges))
            print(json.dumps(timeline(a.src, d["ranges"] if isinstance(d, dict) else d, a.name or None), ensure_ascii=False))
    except ResolveFehler as e:
        sys.exit(f"Resolve: {e}")
