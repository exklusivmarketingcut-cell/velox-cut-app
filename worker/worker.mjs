// Velox Cut – Schnitt-Maschine. Läuft auf einem eigenen Rechner (Beta: dein Mac), nicht auf Vercel.
// Start:  node --env-file=worker/.env worker/worker.mjs
// Holt Aufträge aus Supabase, schneidet sie, lädt die Clips hoch und meldet den Status.
import { createClient } from '@supabase/supabase-js';
import { AwsClient } from 'aws4fetch';
import { spawn } from 'node:child_process';
import { createWriteStream, existsSync, readFileSync, readdirSync, unlinkSync, writeFileSync } from 'node:fs';
import { cp, mkdir, readFile, rename, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir, hostname } from 'node:os';
import { join } from 'node:path';
import { pipeline as streamPipeline } from 'node:stream/promises';
import { Readable } from 'node:stream';

const sb = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_ROLE_KEY, { auth: { persistSession: false } });
const WORKER = process.env.WORKER_NAME || hostname();
const PIPELINE = process.env.PIPELINE || join(import.meta.dirname, 'pipeline.py');
// Alles liegt in EINEM Ordner auf der SSD: …/app (dieser Code) und daneben …/schnitt-maschine
// (Python-Umgebung mit mlx-whisper + OpenCV, Schriften, Protokolle, Zwischendateien)
const MASCHINE = process.env.VELOX_MASCHINE || join(import.meta.dirname, '..', '..', 'schnitt-maschine');
const PYTHON = process.env.PYTHON || join(MASCHINE, 'venv', 'bin', 'python');
const TMP = process.env.WORKER_TMP || (existsSync(MASCHINE) ? join(MASCHINE, 'tmp') : join(tmpdir(), 'velox-worker'));
const POLL_MS = 8000;
// Lernmaterial: Aufträge von Test-Konten (haben diesen Code eingelöst) bleiben mit Rohvideo und Ergebnis auf der SSD
// (…/schnitt-maschine/lern/<Datum>_<Auftrag>), statt nach dem Schnitt gelöscht zu werden. Alle anderen Kunden: wie bisher löschen.
const LERN_CODE = process.env.LERN_CODE || 'TEST-F7LF3Y4H';
const CLAUDE_SCHNITT = ['lern', 'alle'].includes(process.env.CLAUDE_SCHNITT) ? process.env.CLAUDE_SCHNITT : ''; // '' = aus (Standard)
// WARTEZEIT: siehe claudeFristPruefen() – seit 17.10. kein Ausweichen auf die alte Pipeline mehr, nur Warnung an den Inhaber.
// (claude_ausweich.json stammt aus der Zeit davor; dort eingetragene Aufträge schneidet die Maschine noch wie früher.)
const CLAUDE_FRIST_MIN = Number(process.env.CLAUDE_FRIST_MIN || 30);
const AUSWEICH_DATEI = join(MASCHINE, 'claude_ausweich.json');
function ausweichListe() { try { return new Set(JSON.parse(readFileSync(AUSWEICH_DATEI, 'utf8'))); } catch { return new Set(); } }
function claudeAblagen() {   // [{ordner, f, j}] aller abgelegten Claude-Aufträge
  const lern = join(MASCHINE, 'lern');
  if (!existsSync(lern)) return [];
  const r = [];
  for (const d of readdirSync(lern)) {
    const f = join(lern, d, 'freigabe.json');
    if (!existsSync(f)) continue;
    try { const j = JSON.parse(readFileSync(f, 'utf8')); if (j.claude) r.push({ ordner: join(lern, d), f, j }); } catch { /* unlesbar */ }
  }
  return r;
}
// KEIN AUSWEICHEN MEHR AUF GEMINI (Inhaber 17.10.: „über diese Gemini-Pipeline, das ist echt Quatsch – ändere das um, dass du
// das machst, auch wenn das mal hängt“). Claude-Aufträge bleiben IMMER bei Claude. Wartet einer zu lange, wird der Inhaber
// gewarnt (E-Mail + Mitteilung auf dem Mac) und der Kunde sieht „dauert etwas länger“. Stufen: nach CLAUDE_FRIST_MIN (30) Min.
// und nach 2 Std. erneut. Der Auftrag wird nie an die alte Pipeline gegeben.
async function claudeFristPruefen() {
  if (!CLAUDE_SCHNITT) return;
  const offen = claudeAblagen().filter(({ j }) => !j.erledigt);
  for (const { f, j } of offen) {
    const min = (Date.now() - Date.parse(j.wartet_seit)) / 60e3;
    const stufe = min >= 120 ? 2 : min >= CLAUDE_FRIST_MIN ? 1 : 0;
    if (stufe <= (j.gewarnt || 0)) continue;
    const { data: z } = await sb.from('jobs').select('status').eq('id', j.id).single();
    if (z?.status !== 'review') { j.erledigt = 'nicht-mehr-offen'; j.erledigt_am = new Date().toISOString(); await writeFile(f, JSON.stringify(j, null, 1)); continue; }
    j.gewarnt = stufe;
    await writeFile(f, JSON.stringify(j, null, 1));
    await sb.from('jobs').update({ stage: 'Dein Video ist in Arbeit – heute dauert es etwas länger. Du bekommst eine E-Mail, sobald es fertig ist.' }).eq('id', j.id).eq('status', 'review');
    const text = `Auftrag ${j.id} wartet seit ${Math.round(min)} Min. auf den Claude-Schnitt (${offen.length} offen).\n\n` +
      'Bitte prüfen: MacBook an, SSD angesteckt, DaVinci Resolve offen (Workspace → Scripts → resolve_mcp_bridge).\n' +
      (CLAUDE_AUTOSTART ? `Die Maschine startet den Claude-Lauf selbst – Protokoll: <Auftragsordner>/claude_lauf.log und ${join(MASCHINE, 'logs', 'worker.log')}.\n\n`
        : 'In der Claude-App unter „Geplante Aufgaben“ → „Velox Cut – Claude schneidet wartende Aufträge“: hängt ein Lauf an einer Rückfrage, dort „Immer erlauben“ wählen oder „Jetzt ausführen“.\n\n') +
      'Der Auftrag bleibt bei Claude – es wird NICHT auf die alte Pipeline ausgewichen.';
    await mail({ art: 'warnung', betreff: stufe === 2 ? 'Auftrag wartet seit über 2 Std. auf den Schnitt' : 'Auftrag wartet auf den Claude-Schnitt', text });
    spawn('osascript', ['-e', `display notification "Auftrag wartet seit ${Math.round(min)} Min. – Claude-App prüfen" with title "Velox Cut" sound name "Glass"`]).on('error', () => null);
    log('⏱', j.id, `wartet seit ${Math.round(min)} Min. auf den Claude-Schnitt – Inhaber gewarnt (Stufe ${stufe}), bleibt bei Claude`);
  }
}
function ausweichRoh(jobId) {   // Rohmaterial eines Ausweich-Auftrags aus der Ablage
  for (const { ordner, j } of claudeAblagen()) {
    if (j.id === jobId && j.erledigt === 'ausweich' && j.roh) {
      const p = j.roh.startsWith('/') ? j.roh : join(ordner, j.roh);
      if (existsSync(p)) return p;
    }
  }
  return null;
}
async function istLernkonto(userId) {
  if (!LERN_CODE) return false;
  const { data } = await sb.from('code_uses').select('code').eq('code', LERN_CODE).eq('user_id', userId).limit(1);
  return !!data?.length;
}
// wartet = { outputs } → der Auftrag steht auf „review“ (Inhaber 10.10., Weg B): Das Video geht erst an den Kunden, wenn es
// geprüft ist. Freigeben/ersetzen/ablehnen: node --env-file=worker/.env worker/freigeben.mjs (liest freigabe.json).
async function lernablage(job, dir, wartet = null) {
  if (!existsSync(dir) || !(wartet?.claude || await istLernkonto(job.user_id))) return false;
  const { data: j } = await sb.from('jobs').select('status, error, style, clips, outputs, created_at').eq('id', job.id).single();
  const ziel = join(MASCHINE, 'lern', `${new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-')}_${job.id}`);
  await mkdir(join(MASCHINE, 'lern'), { recursive: true });
  await rename(dir, ziel);
  await writeFile(join(ziel, 'auftrag.json'), JSON.stringify({ id: job.id, ...j, user_id: job.user_id, source_kind: job.source_kind, source_name: job.source_name, caption_style: job.caption_style, caption_color: job.caption_color, music: job.music }, null, 1));
  if (wartet) await writeFile(join(ziel, 'freigabe.json'), JSON.stringify({ id: job.id, user_id: job.user_id, wartet_seit: new Date().toISOString(), outputs: wartet.outputs,
    ...(wartet.claude ? { claude: true, roh: wartet.roh, marke: wartet.marke, stil: job.style, logo: wartet.logo, lernkonto: wartet.lernkonto } : {}) }, null, 1));
  log('📚', job.id, 'als Lernmaterial abgelegt');
  if (wartet?.claude) { vorhoeren(job.id, ziel, wartet.roh); claudeStarten(); }
  return true;
}
// SOFORT VORHÖREN (Inhaber 17.10.: Kunden sollen nicht unnötig warten): Transkript und Kontaktbogen entstehen direkt nach dem
// Hochladen im Hintergrund, nicht erst, wenn der Claude-Lauf drankommt. Der Lauf ruft „hoeren“ trotzdem auf – das ist dann in
// Sekunden fertig (Transkripte zwischengespeichert; läuft das Vorhören noch, wartet „hoeren“ per Sperrdatei darauf).
// Immer nur eins zur Zeit (8 GB Arbeitsspeicher).
let vorhoerKette = Promise.resolve();
function vorhoeren(id, ordner, roh) {
  const rohPfad = roh ? (roh.startsWith('/') ? roh : join(ordner, roh)) : ordner;
  vorhoerKette = vorhoerKette.then(() => new Promise((fertig) => {
    let quelle = ordner;
    try { readdirSync(rohPfad); quelle = rohPfad; } catch { /* roh ist eine einzelne Datei → im Auftragsordner hören */ }
    const edit = join(ordner, 'edit');
    const p = spawn(PYTHON, [join(import.meta.dirname, 'claude_schnitt.py'), 'hoeren', quelle, edit], { cwd: MASCHINE, stdio: 'ignore' });
    const t0 = Date.now();
    p.on('close', (code) => { log('👂', id, code === 0 ? `vorgehört (${Math.round((Date.now() - t0) / 1000)} s)` : `Vorhören fehlgeschlagen (Code ${code}) – der Claude-Lauf hört selbst`); fertig(); });
    p.on('error', (e) => { log('👂', id, 'Vorhören nicht gestartet:', e.message); fertig(); });
  }));
}

// CLAUDE-AUSLÖSER (Inhaber 22.10.: „nicht alle 5 Minuten nachgucken, sondern aktivieren, wenn etwas reingelegt wird“). Bisher
// hat eine geplante Aufgabe in der Claude-App alle 5 Min. eine ganze Claude-Sitzung gestartet, nur um nachzusehen – das hat das
// Abo-Limit aufgebraucht, auch wenn nichts da war. Jetzt startet die Maschine selbst einen Claude-Lauf (`claude -p`, ohne
// Chatfenster), sobald ein Auftrag abgelegt ist – genau einen pro Auftrag. Liegt nichts an, läuft kein Claude.
// - Die Maschine nimmt den Auftrag selbst (claude_in_arbeit.txt, wie freigeben.mjs --nehmen), höchstens CLAUDE_PARALLEL Läufe
//   gleichzeitig (Standard 1: 8 GB Arbeitsspeicher, gebaut wird ohnehin nacheinander über bau.lock).
// - Ein Lauf ohne Chatfenster kann nicht an einer Rückfrage hängen bleiben (Test 18.–22.10.: ein Lauf hing 4 Tage) – nicht
//   erlaubte Werkzeuge werden abgelehnt. Nach CLAUDE_LAUF_MAX_MIN (85) Min. wird er beendet.
// - Scheitert ein Lauf, versucht die Maschine es nach 15 Min. erneut: bei Limit/fehlender Anmeldung immer weiter (Mail höchstens
//   alle 3 Std.), bei einem echten Fehlschlag beim Schneiden höchstens 3× – danach Mail.
// - Nach einem Neustart der Maschine werden liegengebliebene Aufträge automatisch nachgeholt.
// Anmeldung: CLAUDE_CODE_OAUTH_TOKEN (aus `claude setup-token`) oder ANTHROPIC_API_KEY in worker/.env.
// Abschalten: CLAUDE_AUTOSTART=0 (dann wieder wie früher über die geplante Aufgabe).
const CLAUDE_AUTOSTART = process.env.CLAUDE_AUTOSTART !== '0';
const CLAUDE_PARALLEL = Math.max(1, Number(process.env.CLAUDE_PARALLEL || 1));
const CLAUDE_LAUF_MAX_MIN = Number(process.env.CLAUDE_LAUF_MAX_MIN || 85);   // unter den 90 Min., nach denen die Markierung verfällt
const CLAUDE_WIEDERHOLEN_MIN = 15;
const CLAUDE_MAX_VERSUCHE = 3;
const HOME = process.env.HOME || '';
const CLAUDE_BIN = process.env.CLAUDE_BIN || [join(HOME, '.local', 'bin', 'claude'), join(HOME, '.claude', 'local', 'claude'), '/opt/homebrew/bin/claude', '/usr/local/bin/claude'].find((p) => existsSync(p)) || 'claude';
// Arbeitsordner wie bei der geplanten Aufgabe: Dort liegen die Freigaben (~/Downloads/.claude/settings.local.json)
const CLAUDE_CWD = process.env.CLAUDE_CWD || join(HOME, 'Downloads');
// Anleitung: der Wortlaut der bisherigen geplanten Aufgabe (dort pflegt der Inhaber die Regeln); fehlt sie, die Kopie im Code
const CLAUDE_ANLEITUNG = [process.env.CLAUDE_ANLEITUNG, join(HOME, '.claude', 'scheduled-tasks', 'velox-claude-schnitt', 'SKILL.md'), join(import.meta.dirname, 'claude_anleitung.md')].find((p) => p && existsSync(p));
const claudeLaeufe = new Map();   // Auftrag → Prozess
let claudeGewarnt = 0;

function claudeBelegt(ordner) {
  try { return Date.now() - Date.parse(readFileSync(join(ordner, 'claude_in_arbeit.txt'), 'utf8')) < 90 * 60e3; } catch { return false; }
}
function claudeNehmen(ordner) {   // wie freigeben.mjs --nehmen: exklusiv anlegen, verfallene Markierung ersetzen
  const m = join(ordner, 'claude_in_arbeit.txt');
  if (claudeBelegt(ordner)) return false;
  try { unlinkSync(m); } catch { /* keine alte */ }
  try { writeFileSync(m, new Date().toISOString(), { flag: 'wx' }); return true; } catch { return false; }
}
// Reihenfolge wie in freigeben.mjs: Clean/Viral zuerst, Premium, das schon 40 Min. wartet, rückt wieder nach vorn
const claudeRang = (j) => (j.stil === 'premium' && (Date.now() - Date.parse(j.wartet_seit)) / 60e3 < 40 ? 1 : 0);

function claudeStarten() {
  if (!CLAUDE_SCHNITT || !CLAUDE_AUTOSTART || !existsSync(MASCHINE)) return;
  const offen = claudeAblagen().filter(({ j, ordner }) => !j.erledigt && !claudeLaeufe.has(j.id) && !claudeBelegt(ordner)
    && !(j.claude_naechster && Date.parse(j.claude_naechster) > Date.now()) && (j.claude_versuche || 0) < CLAUDE_MAX_VERSUCHE)
    .sort((a, b) => claudeRang(a.j) - claudeRang(b.j) || Date.parse(a.j.wartet_seit) - Date.parse(b.j.wartet_seit));
  for (const a of offen) {
    if (claudeLaeufe.size >= CLAUDE_PARALLEL) return;
    if (claudeNehmen(a.ordner)) claudeLauf(a);
  }
}

function claudeLauf({ ordner, f, j }) {
  const anleitung = CLAUDE_ANLEITUNG ? readFileSync(CLAUDE_ANLEITUNG, 'utf8') : '';
  const prompt = `Du schneidest GENAU EINEN Auftrag von Velox Cut: ${j.id}\nOrdner: ${ordner}\nStil: ${j.stil || '?'}\n\n` +
    'Die Anleitung unten ist die der früheren geplanten Aufgabe. Folge ihr vollständig, mit diesen Abweichungen:\n' +
    '- Die Liste wartender Aufträge NICHT abrufen und keinen anderen Auftrag bearbeiten – nur diesen.\n' +
    '- Der Auftrag ist schon für dich genommen: freigeben.mjs --nehmen, --loslassen, --start und --ende NICHT aufrufen.\n' +
    '- Am Ende entweder freigeben (`freigeben.mjs <Auftrag> "1=<Ordner>/edit/final.mp4"`) oder ablehnen (`--ablehnen`).\n' +
    '- Nichts außerhalb von schnitt-maschine/lern/ schreiben; das Protokoll gehört nach <Ordner>/protokoll.md.\n\n' +
    `--- ANLEITUNG (${CLAUDE_ANLEITUNG || 'fehlt'}) ---\n${anleitung}`;
  // Nur, was Claude braucht – die Schlüssel der Maschine (Supabase, R2, Stripe …) gehen nicht an den Lauf
  const env = {};
  for (const k of ['PATH', 'HOME', 'USER', 'LOGNAME', 'SHELL', 'LANG', 'LC_ALL', 'TMPDIR', 'CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_API_KEY']) if (process.env[k]) env[k] = process.env[k];
  env.PATH = `${join(HOME, '.local', 'bin')}:${env.PATH || '/usr/bin:/bin'}`;
  const logDatei = join(ordner, 'claude_lauf.log');
  const out = createWriteStream(logDatei, { flags: 'a' });
  out.write(`\n=== ${new Date().toISOString()} Lauf ${(j.claude_versuche || 0) + 1} ===\n`);
  const t0 = Date.now();
  let p;
  try {
    p = spawn(CLAUDE_BIN, ['-p', prompt], { cwd: existsSync(CLAUDE_CWD) ? CLAUDE_CWD : MASCHINE, env, stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (e) { claudeLaufEnde({ ordner, f, j }, -1, e.message, t0); return; }
  claudeLaeufe.set(j.id, p);
  log('🤖', j.id, `Claude-Lauf gestartet (${claudeLaeufe.size}/${CLAUDE_PARALLEL})`);
  let rest = '';
  const merken = (d) => { out.write(d); rest = (rest + d).slice(-2000); };
  p.stdout.on('data', merken);
  p.stderr.on('data', merken);
  const stopp = setTimeout(() => { log('🤖', j.id, `Claude-Lauf nach ${CLAUDE_LAUF_MAX_MIN} Min. beendet`); p.kill('SIGTERM'); }, CLAUDE_LAUF_MAX_MIN * 60e3);
  p.on('error', (e) => { clearTimeout(stopp); out.end(); claudeLaufEnde({ ordner, f, j }, -1, e.message, t0); });
  p.on('close', (code) => { clearTimeout(stopp); out.end(); claudeLaufEnde({ ordner, f, j }, code, rest, t0); });
}

async function claudeLaufEnde({ ordner, f, j }, code, rest, t0) {
  if (!claudeLaeufe.has(j.id) && code !== -1) return;
  claudeLaeufe.delete(j.id);
  const min = Math.round((Date.now() - t0) / 60e3);
  let neu = j;
  try { neu = JSON.parse(readFileSync(f, 'utf8')); } catch { /* unlesbar → wie vorher */ }
  if (neu.erledigt) {
    log('🤖', j.id, `Claude-Lauf fertig (${neu.erledigt}, ${min} Min.)`);
  } else {
    try { unlinkSync(join(ordner, 'claude_in_arbeit.txt')); } catch { /* schon frei */ }
    const grund = /usage limit|limit reached|rate.?limit/i.test(rest) ? 'Claude-Limit erreicht'
      : /401|unauthori[sz]ed|invalid api key|log ?in|authenticat/i.test(rest) ? 'Claude ist auf dem Mac nicht angemeldet (claude setup-token)'
      : /ENOENT|not found/i.test(rest) && code === -1 ? `Claude-Programm nicht gefunden (${CLAUDE_BIN})` : `Lauf endete ohne Freigabe (Code ${code})`;
    // Limit/Anmeldung/Programm: am Auftrag liegt es nicht – zählt nicht als Versuch, es geht alle 15 Min. weiter (kostet nichts,
    // Claude lehnt sofort ab). Nur echte Fehlschläge beim Schneiden zählen bis CLAUDE_MAX_VERSUCHE.
    const allgemein = grund.startsWith('Lauf endete');
    if (allgemein) neu.claude_versuche = (neu.claude_versuche || 0) + 1;
    neu.claude_naechster = new Date(Date.now() + CLAUDE_WIEDERHOLEN_MIN * 60e3).toISOString();
    await writeFile(f, JSON.stringify(neu, null, 1)).catch(() => null);
    const versuche = neu.claude_versuche || 0;
    log('🤖', j.id, `${grund}${allgemein ? ` – Versuch ${versuche}/${CLAUDE_MAX_VERSUCHE}` : ''}${!allgemein || versuche < CLAUDE_MAX_VERSUCHE ? `, nächster in ${CLAUDE_WIEDERHOLEN_MIN} Min.` : ''}`);
    if ((allgemein && versuche >= CLAUDE_MAX_VERSUCHE) || (!allgemein && Date.now() - claudeGewarnt > 3 * 3600e3)) {
      claudeGewarnt = Date.now();
      await mail({ art: 'warnung', betreff: 'Claude-Schnitt: Lauf gescheitert', text: `Auftrag ${j.id}: ${grund}.\n` +
        (allgemein ? `Versuch ${versuche} von ${CLAUDE_MAX_VERSUCHE} – die Maschine versucht es nicht mehr selbst. Ordner prüfen; zum erneuten Versuch claude_versuche in freigabe.json löschen.`
          : `Die Maschine versucht es alle ${CLAUDE_WIEDERHOLEN_MIN} Min. weiter, bis es klappt.`) +
        `\n\nProtokoll: ${join(ordner, 'claude_lauf.log')}\nLetzte Ausgabe:\n${rest.slice(-800)}` });
    }
  }
  claudeStarten();
}

// Video-Speicher Cloudflare R2 (S3-kompatibel): uploads/<pfad> und outputs/<pfad>
const r2 = new AwsClient({ accessKeyId: process.env.R2_ACCESS_KEY_ID, secretAccessKey: process.env.R2_SECRET_ACCESS_KEY, service: 's3', region: 'auto' });
const R2_BASE = `${process.env.R2_ENDPOINT || `https://${process.env.R2_ACCOUNT_ID}.r2.cloudflarestorage.com`}/${process.env.R2_BUCKET}`;
const r2url = (key) => `${R2_BASE}/${key.split('/').map(encodeURIComponent).join('/')}`;
const r2get = (key) => r2.fetch(r2url(key));
const r2put = (key, body) => r2.fetch(r2url(key), { method: 'PUT', body, headers: { 'Content-Type': 'video/mp4' } });
const r2del = (key) => r2.fetch(r2url(key), { method: 'DELETE' }).catch(() => null);

const log = (...a) => console.log(new Date().toLocaleTimeString('de-DE'), ...a);
const setJob = (id, patch) => sb.from('jobs').update(patch).eq('id', id);
// Arbeitsschritt fürs Studio melden (Spalte „stage“ – fehlt sie noch, einfach weglassen)
let stageOk = true;
const setStage = async (id, stage) => {
  if (!stageOk) return;
  const { error } = await setJob(id, { stage });
  if (error) stageOk = false;
};

// Lebenszeichen für den Wächter in der Datenbank (alle 30 Sekunden): lebt die Maschine, woran arbeitet sie, hängt die Pipeline?
const beat = { busy_job: null, last_output: null };
const STARTED = new Date().toISOString();
async function heartbeat(patch = {}) {
  Object.assign(beat, patch);
  const { error } = await sb.from('workers').upsert({ name: WORKER, last_seen: new Date().toISOString(), started_at: STARTED, ...beat });
  if (error && !heartbeat.warned) { heartbeat.warned = true; log('Lebenszeichen nicht möglich:', error.message); }
}
setInterval(() => heartbeat().catch(() => null), 30_000);

// Fehlermeldungen für Kunden: eigene (deutsche) Meldungen der Pipeline durchreichen, technische Fehler freundlich umschreiben
const friendly = (msg) => (/Traceback|Error\b|Exception|errno|ffmpeg|\.py\b|undefined|null|Pipeline|fetch failed|ECONN|ETIMEDOUT/i.test(msg) || !msg
  ? 'Beim Schneiden ist ein Fehler aufgetreten. Bitte starte den Auftrag noch einmal'
  : msg.replace(/[.\s]+$/, ''));
// Pipeline hängt (keine Rückmeldung) → abbrechen, damit die Maschine frei wird
const HANG_MS = 30 * 60e3;

// E-Mails verschickt die App (Resend-Schlüssel liegt nur dort); die Maschine meldet sich mit dem service_role-Schlüssel
const APP_URL = process.env.APP_URL || 'https://velox-cut.de';
async function mail(body) {
  try {
    const r = await fetch(`${APP_URL}/api/intern/mail`, {
      method: 'POST', body: JSON.stringify(body),
      headers: { Authorization: `Bearer ${process.env.SUPABASE_SERVICE_ROLE_KEY}`, 'Content-Type': 'application/json' },
    });
    if (!r.ok) log('E-Mail nicht gesendet:', r.status, (await r.text()).slice(0, 120));
  } catch (e) { log('E-Mail nicht gesendet:', e.message); }
}

// KI-Wächter: Ohne KI (Guthaben leer, Google-Störung) würden Videos ohne Satzauswahl, Lektorat und Abnahme geschnitten –
// dann lieber warten. Aufträge bleiben in der Schlange, der Betreiber bekommt eine E-Mail (höchstens alle 3 Stunden).
const ai = { ok: true, checked: 0, warned: 0, since: 0 };
async function aiReady() {
  // Gratis-Schlüssel (Projekt ohne Zahlungsdaten) zuerst, Prepay-Schlüssel als Reserve – bereit, wenn einer antwortet
  const keys = [process.env.GEMINI_API_KEY_FREE, process.env.GEMINI_API_KEY].map((k) => (k || '').trim()).filter(Boolean);
  if (!keys.length) return true;
  if (Date.now() - ai.checked < (ai.ok ? 10 * 60e3 : 2 * 60e3)) return ai.ok;
  ai.checked = Date.now();
  let code = 0;
  for (const key of keys) {
    for (const model of ['gemini-flash-lite-latest', 'gemini-flash-latest']) {
      try {
        const r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, {
          method: 'POST', signal: AbortSignal.timeout(30_000),
          headers: { 'x-goog-api-key': key, 'Content-Type': 'application/json' },
          body: JSON.stringify({ contents: [{ parts: [{ text: 'ok' }] }], generationConfig: { maxOutputTokens: 1 } }),
        });
        code = r.status;
      } catch { code = -1; }
      if (code === 200 || code === 503) break;
    }
    if (code === 200 || code === 503) break;
  }
  const was = ai.ok;
  ai.ok = code === 200 || code === 503; // 503 = kurz überlastet (die Pipeline wiederholt selbst); 429 = Kontingent erschöpft
  if (!ai.ok && was) { ai.since = Date.now(); log(`KI nicht erreichbar (HTTP ${code}) – Aufträge warten`); }
  if (ai.ok && !was) {
    log('KI wieder erreichbar – es geht weiter');
    await mail({ art: 'warnung', betreff: 'KI wieder erreichbar', text: `Die KI antwortet wieder. Wartende Aufträge werden jetzt geschnitten (Ausfall ca. ${Math.round((Date.now() - ai.since) / 60e3)} Min.).` });
  }
  if (!ai.ok && Date.now() - ai.warned > 3 * 3600e3) {
    ai.warned = Date.now();
    const grund = code === 429 ? 'Das kostenlose Tageskontingent bei Google (Gemini) ist aufgebraucht – morgen geht es automatisch weiter. Sollen Aufträge sofort laufen, im Prepay-Projekt Guthaben aufladen.'
      : code === 402 ? 'Das Guthaben bei Google (Gemini) ist aufgebraucht und kein Gratis-Schlüssel ist eingerichtet (scripts/gemini-gratis-schluessel.sh).'
      : code === 401 || code === 403 ? 'Der Gemini-Schlüssel wird abgelehnt (ungültig oder gesperrt).'
      : `Google antwortet nicht richtig (HTTP ${code}).`;
    await mail({ art: 'warnung', betreff: 'KI nicht erreichbar – Aufträge warten', text: `${grund}\n\nNeue Aufträge bleiben so lange in der Warteschlange und starten automatisch, sobald die KI wieder antwortet. Kunden sehen „Gerade viel los – startet automatisch“ und bekommen eine Mail, wenn ihr Video fertig ist.` });
  }
  return ai.ok;
}

/** YouTube-Link? (youtube.com/watch, youtu.be, Shorts, Live) → Video-ID, sonst null */
function youtubeId(url) {
  try {
    const u = new URL(url);
    const h = u.hostname.replace(/^(www|m|music)\./, '');
    if (h === 'youtu.be') return u.pathname.slice(1).split('/')[0] || null;
    if (h !== 'youtube.com') return null;
    return u.searchParams.get('v') || /^\/(shorts|live|embed)\/([\w-]{6,})/.exec(u.pathname)?.[2] || null;
  } catch { return null; }
}

/** YouTube-Video herunterladen (yt-dlp, höchstens 1080p und 3 Stunden).
 *  Test 14.10.: Ein Abruf brach nach 20 MB ab, derselbe Link lief von Hand sofort durch – YouTube bricht Downloads ab und zu
 *  ab. Deshalb bis zu drei Versuche (yt-dlp setzt die angefangene Datei fort); der echte Fehler kommt ins Protokoll. */
async function ytDownload(job, id, file) {
  for (let v = 1; ; v++) {
    try { return await ytDownloadEinmal(job, id, file); } catch (e) {
      log('YouTube-Abruf', job.id, `Versuch ${v}:`, (e.detail || e.message).slice(-400).replace(/\s+/g, ' '));
      if (v >= 3 || e.endgueltig) throw e;
      await new Promise((r) => setTimeout(r, 5000 * v));
    }
  }
}
function ytDownloadEinmal(job, id, file) {
  const bin = process.env.YTDLP || ['/opt/homebrew/bin/yt-dlp', '/usr/local/bin/yt-dlp'].find((p) => existsSync(p)) || 'yt-dlp';
  return new Promise((resolve, reject) => {
    const p = spawn(bin, ['--no-playlist', '--no-progress', '--match-filter', 'duration<=10800 & !is_live',
      '-f', 'bv*[vcodec^=avc1][height<=1080]+ba[ext=m4a]/bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b', // H.264 zuerst (AV1 kann nicht jeder Schritt lesen) '--merge-output-format', 'mp4',
      '-o', file, `https://www.youtube.com/watch?v=${id}`], { stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env, PATH: `/opt/homebrew/bin:/usr/local/bin:${process.env.PATH || ''}` } });
    let err = '';
    p.stdout.on('data', () => { beat.last_output = new Date().toISOString(); });
    p.stderr.on('data', (b) => { beat.last_output = new Date().toISOString(); err = (err + b).slice(-1500); });
    p.on('error', () => reject(new Error('YouTube-Download ist auf der Schnitt-Maschine nicht eingerichtet.')));
    p.on('close', (code) => {
      if (code === 0 && existsSync(file)) return resolve();
      const privat = /Private video|Sign in|members-only|age/i.test(err);
      const zuLang = /does not pass filter/i.test(err);
      const e = new Error(privat ? 'Dieses YouTube-Video ist privat oder nur mit Anmeldung abrufbar.'
        : 'YouTube-Video nicht abrufbar – stimmt der Link? (Live-Übertragungen und Videos über 3 Stunden gehen nicht.)');
      e.detail = err; e.endgueltig = privat || zuLang;
      reject(e);
    });
  });
}

/** Google-Drive-Freigabelink → direkter Download */
function driveDirect(url) {
  const id = /\/d\/([\w-]{20,})/.exec(url)?.[1] || new URL(url).searchParams.get('id');
  if (!id) throw new Error('Google-Drive-Link nicht erkannt.');
  return `https://drive.usercontent.google.com/download?id=${id}&export=download&confirm=t`;
}

async function download(job, dir) {
  const ausweich = ausweichRoh(job.id);
  if (ausweich) {   // Uploads sind bei der Übergabe an Claude schon gelöscht → Rohmaterial aus der Ablage nehmen
    const ziel = join(dir, ausweich.split('/').pop());
    await cp(ausweich, ziel, { recursive: true });
    return ziel;
  }
  if (job.source_kind === 'folder') { // mehrere Clips + Bilder → eigener Eingangsordner
    const inDir = join(dir, 'eingang');
    await mkdir(inDir, { recursive: true });
    // 4 Dateien gleichzeitig laden (Test 17.10.: 16 Dateien nacheinander = gut 4 Min. bis zur Übergabe)
    const liste = [...(job.source_paths || [])];
    const lade = async () => {
      for (let p = liste.shift(); p; p = liste.shift()) {
        const r = await r2get(`uploads/${p}`);
        if (!r.ok) throw new Error('Eine hochgeladene Datei fehlt.');
        await streamPipeline(Readable.fromWeb(r.body), createWriteStream(join(inDir, p.split('/').pop())));
      }
    };
    await Promise.all([lade(), lade(), lade(), lade()]);
    return inDir;
  }
  if (job.source_kind === 'drive' && /\/folders\//.test(job.source_url || '')) { // ganzer Drive-Ordner → wie Ordner-Upload
    const inDir = join(dir, 'eingang');
    await runPy(job, [join(import.meta.dirname, 'drive.py'), job.source_url, inDir]);
    return inDir;
  }
  const file = join(dir, 'source.mp4');
  if (job.source_kind !== 'upload' && youtubeId(job.source_url || '')) {
    await setStage(job.id, 'Lädt das YouTube-Video …');
    await ytDownload(job, youtubeId(job.source_url), file);
  } else if (job.source_kind === 'upload') {
    const r = await r2get(`uploads/${job.source_path}`);
    if (!r.ok) throw new Error('Upload nicht gefunden.');
    await streamPipeline(Readable.fromWeb(r.body), createWriteStream(file));
  } else {
    const r = await fetch(driveDirect(job.source_url), { redirect: 'follow' });
    const type = r.headers.get('content-type') || '';
    if (!r.ok || type.includes('text/html')) throw new Error('Drive-Datei nicht abrufbar – ist sie für „Jeder mit dem Link“ freigegeben?');
    await streamPipeline(Readable.fromWeb(r.body), createWriteStream(file));
  }
  if ((await stat(file)).size < 10_000) throw new Error('Die Datei ist leer oder beschädigt.');
  return file;
}

/** Logo des Kunden aus dem Speicher holen (liegt unter logos/<user>/…) */
async function downloadLogo(job, dir) {
  const { data } = await sb.from('profiles').select('logo_path').eq('id', job.user_id).single();
  if (!data?.logo_path) return null;
  const r = await r2get(data.logo_path);
  if (!r.ok) return null;
  const file = join(dir, `logo.${data.logo_path.split('.').pop()}`);
  await streamPipeline(Readable.fromWeb(r.body), createWriteStream(file));
  return file;
}

/** Trend-Songs (Shazam-Charts DE) einmal pro Woche auffrischen – für die Song-Vorschläge zu jedem Video */
async function refreshTrends() {
  const file = join(MASCHINE, 'trends.json');
  const age = existsSync(file) ? Date.now() - (await stat(file)).mtimeMs : Infinity;
  if (age < 7 * 864e5) return;
  await new Promise((resolve) => {
    const p = spawn(PYTHON, [join(import.meta.dirname, 'trends.py')], { stdio: ['ignore', 'pipe', 'pipe'], env: { VELOX_MASCHINE: MASCHINE, ...process.env } });
    p.stdout.on('data', (b) => log('🎵', String(b).trim()));
    p.stderr.on('data', (b) => log('🎵', String(b).trim().slice(0, 200)));
    p.on('close', resolve);
  });
}

/** Pipeline starten (Python). Meldet Fortschritt als Zeilen „PROGRESS 42“, Ergebnis als letzte JSON-Zeile. */
/** Mehrere Clips: montage.py plant den Schnitt und setzt die Sprech-Stellen zu EINER Datei zusammen → { src, plan } */
function runMontage(job, inDir, dir, words = '') {
  return new Promise((resolve, reject) => {
    const args = [join(import.meta.dirname, 'montage.py'), '--out', join(dir, 'montage'), '--style', job.style, '--clips', '1', // ein Ordner = ein Video (Inhaber 06.10.)
      ...(words ? ['--words', words.slice(0, 300)] : []), inDir];
    const p = spawn(PYTHON, args, { stdio: ['ignore', 'pipe', 'pipe'], env: { VELOX_MASCHINE: MASCHINE, ...process.env, ...jobEnv, PYTHONUNBUFFERED: '1' } });
    let last = '';
    let errTxt = '';
    p.stdout.on('data', (b) => {
      beat.last_output = new Date().toISOString();
      for (const line of String(b).split('\n')) {
        const st = /^STAGE (.+)$/.exec(line);
        if (st) setStage(job.id, st[1].trim());
        else if (line.trim()) last = line.trim();
      }
    });
    p.stderr.on('data', (b) => { beat.last_output = new Date().toISOString(); errTxt = (errTxt + b).slice(-2000); });
    p.on('close', (code) => {
      if (code === 75) return reject(new Error('KI_KONTINGENT')); // KI erschöpft → Auftrag später wiederholen
      if (code !== 0) return reject(new Error(errTxt.split('\n').filter(Boolean).pop() || `Montage-Fehler ${code}`));
      try { resolve(JSON.parse(last)); } catch { reject(new Error('Montage lieferte kein Ergebnis.')); }
    });
  });
}

function runPipeline(job, src, dir, logo, words = '', plan = null, clips = job.clips, skip = 0) {
  const args = [PIPELINE, '--src', src, '--out', dir, '--style', job.style, '--clips', String(clips), '--caption', job.caption_style, '--color', job.caption_color];
  if (skip > 0) args.push('--skip', String(skip)); // derselbe Link wurde schon geschnitten → nächstbester Clip
  if (plan) args.push('--plan', plan); // Schnittplan aus mehreren Clips (B-Rolls, Bilder, Clip-Grenzen)
  if (job.music === 'auto') args.push('--music', 'auto'); // lizenzfreie Hintergrundmusik (schnitt-maschine/musik)
  if (logo) args.push('--logo', logo); // Kunden-Logo oben links
  if (words) args.push('--words', words.slice(0, 300)); // Namen & Marken → richtige Untertitel
  return runPy(job, args);
}

/** Nur B-Roll (niemand spricht): musikschnitt.py schneidet auf den Beat – Ergebnis im selben Format wie die Pipeline */
function runMusikschnitt(job, inDir, dir, logo, words = '') {
  const args = [join(import.meta.dirname, 'musikschnitt.py'), '--out', join(dir, 'musik'), '--style', job.style];
  if (logo) args.push('--logo', logo);
  if (words) args.push('--words', words.slice(0, 300));
  return runPy(job, [...args, inDir]);
}

function runPy(job, args) {
  return new Promise((resolve, reject) => {
    const p = spawn(PYTHON, args, { stdio: ['ignore', 'pipe', 'pipe'], env: { VELOX_MASCHINE: MASCHINE, ...process.env, ...jobEnv, PYTHONUNBUFFERED: '1' } });
    let last = '';
    let errTxt = '';
    let lastOut = Date.now();
    const touch = () => { lastOut = Date.now(); beat.last_output = new Date().toISOString(); };
    const hang = setInterval(() => {
      if (Date.now() - lastOut > HANG_MS) { errTxt += '\nPipeline hing (keine Rückmeldung) – abgebrochen'; p.kill('SIGKILL'); }
    }, 60_000);
    p.stdout.on('data', (b) => {
      touch();
      for (const line of String(b).split('\n')) {
        const m = /^PROGRESS (\d+)/.exec(line);
        const st = /^STAGE (.+)$/.exec(line);
        if (m) setJob(job.id, { progress: Math.min(99, +m[1]) });
        else if (st) setStage(job.id, st[1].trim());
        else if (line.trim()) last = line.trim();
      }
    });
    p.stderr.on('data', (b) => { touch(); errTxt = (errTxt + b).slice(-2000); });
    p.on('close', (code) => {
      clearInterval(hang);
      if (code === 75) return reject(new Error('KI_KONTINGENT')); // KI erschöpft → Auftrag später wiederholen
      if (code !== 0) return reject(new Error(errTxt.split('\n').filter(Boolean).pop() || `Pipeline-Fehler ${code}`));
      try { resolve(JSON.parse(last)); } catch { reject(new Error('Pipeline lieferte kein Ergebnis.')); }
    });
  });
}

// Änderungswunsch (Revision, Inhaber 11.10.): Der Wunsch des Kunden und der Text der bisherigen Fassung gehen als
// Umgebungsvariablen an die Schnitt-Programme; ki.py hängt sie an jede KI-Anweisung („nur das Gewünschte ändern“).
let jobEnv = {};
const ROH_STUNDEN = 24; // so lange bleibt das Rohmaterial nach dem Schnitt für Änderungswünsche liegen

async function processJob(job) {
  let wartet = null;
  const rev = !!job.revision_note && (job.revisions ?? 0) > 0 && Array.isArray(job.outputs) && job.outputs.length > 0;
  jobEnv = rev ? { VELOX_KUNDENWUNSCH: String(job.revision_note).slice(0, 600), VELOX_VORHER: job.outputs.map((o) => o.text || '').filter(Boolean).join('\n\n').slice(0, 6000) } : {};
  if (rev) log('✏️', job.id, `Änderungswunsch Nr. ${job.revisions}: ${String(job.revision_note).slice(0, 80)}`);
  const dir = join(TMP, job.id);
  await mkdir(dir, { recursive: true });
  log('▶', job.id, job.style, `${job.clips} Clip(s)`, job.source_name ?? '');
  await heartbeat({ busy_job: job.id, last_output: new Date().toISOString() });
  try {
    await setStage(job.id, 'Lädt dein Video …');
    let src = await download(job, dir);
    await setJob(job.id, { progress: 10 });
    const logo = job.use_logo ? await downloadLogo(job, dir).catch(() => null) : null;
    // Namen & Marken des Kunden (Spalte fehlt evtl. noch → einfach ohne)
    const { data: pw } = await sb.from('profiles').select('brand_words').eq('id', job.user_id).single();
    // CLAUDE-SCHNITT (Inhaber 13.10.2026: „Claude soll Augen, Ohren und der Analysator für die Videos sein – nicht mehr Google
    // AI Studio“). Mit CLAUDE_SCHNITT=lern (nur Lernkonto) oder CLAUDE_SCHNITT=alle schneidet die Maschine nicht selbst: Sie legt
    // das Rohmaterial ab und stellt den Auftrag auf „review“. Die geplante Aufgabe in der Claude-App schneidet ihn nach
    // notizen/claude-schnitt.md (claude_schnitt.py) und gibt ihn mit freigeben.mjs frei. Ohne die Variable bleibt alles wie bisher.
    const ausweichen = ausweichListe().has(job.id);
    if (CLAUDE_SCHNITT && !rev && !ausweichen) {
      const lk = await istLernkonto(job.user_id).catch(() => false);
      if (CLAUDE_SCHNITT === 'alle' || lk) {
        const outputs = [{ path: `${job.user_id}/${job.id}/clip-1.mp4`, title: job.source_name || 'Dein Video', seconds: 0, songs: [], post: null }];
        const { data: rv, error: re } = await setJob(job.id, { status: 'review', progress: 40, stage: 'Dein Video wird gerade geschnitten – du bekommst eine E-Mail, sobald es fertig ist' }).eq('status', 'processing').select('id');
        if (!re && rv?.length) {
          wartet = { outputs, claude: true, roh: src.startsWith(dir) ? src.slice(dir.length + 1) : src, marke: pw?.brand_words || '', logo: logo ? (logo.startsWith(dir) ? logo.slice(dir.length + 1) : logo) : null, lernkonto: lk };
          if (job.source_path) { await r2del(`uploads/${job.source_path}`); await setJob(job.id, { source_path: null }); }
          if (job.source_paths?.length) { for (const p of job.source_paths) await r2del(`uploads/${p}`); await setJob(job.id, { source_paths: [] }); }
          log('🎬', job.id, 'wartet auf den Claude-Schnitt');
          return;
        }
        if (re) log('Claude-Schnitt: Prüf-Status nicht möglich (0011.sql ausgeführt?) – schneide wie bisher:', re.message);
      }
    }
    let plan = null;
    let result = null; // { clips: [{file, title, seconds, songs}] }
    const many = job.source_kind === 'folder' || (await stat(src)).isDirectory(); // Ordner-Upload oder Drive-Ordner
    if (many) {
      const m = await runMontage(job, src, dir, pw?.brand_words || '');
      if (m.musik) result = await runMusikschnitt(job, src, dir, logo, pw?.brand_words || ''); // nur B-Roll → auf den Beat
      else if (m.videos?.length > 1) { // mehrere Themen im Ordner → je Thema ein eigenes Video
        result = { clips: [] };
        for (const [i, v] of m.videos.entries()) {
          await setStage(job.id, `Schneidet Video ${i + 1} von ${m.videos.length} …`);
          const sub = join(dir, `video${i + 1}`);
          await mkdir(sub, { recursive: true });
          const r = await runPipeline(job, v.src, sub, logo, pw?.brand_words || '', v.plan, 1).catch((e) => { log('Video', i + 1, e.message); return null; });
          result.clips.push(...(r?.clips ?? []));
        }
      } else {
        src = m.src;
        plan = m.plan;
      }
    }
    if (!result) {
      try {
        // Derselbe Link (YouTube/Drive) wurde von diesem Kunden schon geschnitten → die schon gelieferten Clips überspringen
        let skip = 0;
        if (!plan && job.source_kind === 'drive' && job.source_url) {
          const key = youtubeId(job.source_url) || job.source_url;
          const { data: alt } = await sb.from('jobs').select('source_url, clips').eq('user_id', job.user_id).eq('status', 'done').neq('id', job.id).limit(200);
          skip = (alt ?? []).filter((j) => j.source_url && (youtubeId(j.source_url) || j.source_url) === key).reduce((a, j) => a + (j.clips || 1), 0);
        }
        result = await runPipeline(job, src, dir, logo, pw?.brand_words || '', plan, plan ? 1 : job.clips, skip); // Montage = eine Geschichte = ein Video
      } catch (e) { // einzelnes Video ohne Sprache (nur B-Roll) → Musik-Schnitt statt Fehlermeldung
        if (plan || !/keine Sprache|keine Tonspur/.test(String(e?.message)) || e?.message === 'KI_KONTINGENT') throw e;
        result = await runMusikschnitt(job, src, dir, logo, pw?.brand_words || '');
      }
    }
    if (!result.clips?.length) throw new Error('Es konnten keine Clips erstellt werden.');
    const outputs = [];
    for (const [i, c] of result.clips.entries()) {
      const path = `${job.user_id}/${job.id}/clip-${i + 1}.mp4`;
      const up = await r2put(`outputs/${path}`, await readFile(c.file));
      if (!up.ok) throw new Error('Hochladen des Clips fehlgeschlagen.');
      // Text der fertigen Fassung merken – ein späterer Änderungswunsch baut darauf auf
      const text = await readFile(c.file.replace(/\.mp4$/, '.woerter.json'), 'utf8').then((t) => JSON.parse(t).map((w) => w[0]).join(' ').slice(0, 4000)).catch(() => '');
      outputs.push({ path, title: c.title || `Clip ${i + 1}`, seconds: c.seconds, songs: c.songs ?? [], post: c.post ?? null, ...(text ? { text } : {}) });
    }
    // weniger Clips als bezahlt → Differenz erstatten
    if (outputs.length < job.clips && !rev) {
      const per = Math.round(job.credits_charged / job.clips);
      await sb.rpc('refund_credits', { p_user: job.user_id, p_amount: per * (job.clips - outputs.length), p_ref: `${job.id}-teil` });
    }
    // Lernkonto: nicht ausliefern, sondern auf „review“ stellen (Clips liegen schon im Speicher, stehen aber noch nicht am
    // Auftrag). Kennt die Datenbank den Status noch nicht (0011.sql fehlt), wird wie gewohnt ausgeliefert.
    if (!ausweichen && await istLernkonto(job.user_id).catch(() => false)) {
      const { data: rv, error: re } = await setJob(job.id, { status: 'review', progress: 99, stage: 'Letzte Qualitätsprüfung – du bekommst eine E-Mail, sobald dein Video freigegeben ist' }).eq('status', 'processing').select('id');
      if (!re && rv?.length) {
        wartet = { outputs };
        if (job.source_path) { await r2del(`uploads/${job.source_path}`); await setJob(job.id, { source_path: null }); }
        if (job.source_paths?.length) { for (const p of job.source_paths) await r2del(`uploads/${p}`); await setJob(job.id, { source_paths: [] }); }
        log('⏳', job.id, `${outputs.length} Clip(s) – wartet auf Prüfung`);
        return;
      }
      if (re) log('Prüf-Status nicht möglich (0011.sql ausgeführt?):', re.message);
    }
    // nur abschließen, wenn der Wächter den Auftrag nicht schon beendet (und erstattet) hat
    const { data: fin } = await setJob(job.id, { status: 'done', progress: 100, outputs, finished_at: new Date().toISOString() }).eq('status', 'processing').select('id');
    if (!fin?.length) {
      log('⚠', job.id, 'war schon vom Wächter beendet – Clips verworfen');
      for (const o of outputs) await r2del(`outputs/${o.path}`);
      return;
    }
    // Rohvideo sofort löschen – wird nicht mehr gebraucht (Datenschutz, Speicherplatz)
    // Rohvideo noch 24 Stunden behalten (für einen Änderungswunsch), dann löscht cleanup() es. Kennt die Datenbank die Spalte
    // noch nicht (0012.sql fehlt), wird wie früher sofort gelöscht (Datenschutz, Speicherplatz).
    const { error: ke } = await setJob(job.id, { keep_until: new Date(Date.now() + ROH_STUNDEN * 3600e3).toISOString(), revision_note: null });
    if (ke) {
      if (job.source_path) { await r2del(`uploads/${job.source_path}`); await setJob(job.id, { source_path: null }); }
      if (job.source_paths?.length) { for (const p of job.source_paths) await r2del(`uploads/${p}`); await setJob(job.id, { source_paths: [] }); }
    }
    log('✓', job.id, `${outputs.length} Clip(s)`);
    await mail({ art: 'fertig', job: job.id }); // „Dein Video ist fertig“ – Kunde muss das Studio nicht offen lassen
  } catch (e) {
    if (e.message === 'KI_KONTINGENT') { // nicht ohne KI ausliefern: zurück in die Schlange, kein Fehler, keine Erstattung nötig
      log('⏸', job.id, 'KI-Kontingent erschöpft – Auftrag wartet');
      ai.ok = false; ai.checked = Date.now(); ai.since = ai.since || Date.now();
      await setJob(job.id, { status: 'queued', progress: 0, stage: 'Gerade viel los – startet automatisch, du bekommst eine E-Mail, sobald dein Video fertig ist', worker: null, started_at: null }).eq('status', 'processing');
      return;
    }
    log('✗', job.id, e.message);
    if (rev) { // Änderung gescheitert: Das bisherige Video bleibt, nur die Credits dieser Änderung gehen zurück
      await setJob(job.id, { status: 'done', progress: 100, revision_note: null, stage: 'Die Änderung hat nicht geklappt – dein bisheriges Video ist unverändert' }).eq('status', 'processing');
      if (job.revision_cost > 0) await sb.rpc('refund_credits', { p_user: job.user_id, p_amount: job.revision_cost, p_ref: `${job.id}-rev${job.revisions}` });
      return;
    }
    await setJob(job.id, { status: 'failed', error: friendly(String(e.message)).slice(0, 300), finished_at: new Date().toISOString() }).eq('status', 'processing');
    await sb.rpc('refund_job', { p_job: job.id });
  } finally {
    await heartbeat({ busy_job: null, last_output: null }).catch(() => null);
    if (!(await lernablage(job, dir, wartet).catch((e) => { log('Lernablage:', e.message); return false; }))) await rm(dir, { recursive: true, force: true });
  }
}

/** Datenschutz: nicht geschnittene Uploads nach 7 Tagen, fertige Clips nach 90 Tagen löschen. */
async function cleanup() {
  const d7 = new Date(Date.now() - 7 * 864e5).toISOString();
  const d90 = new Date(Date.now() - 90 * 864e5).toISOString();
  const { data: olds } = await sb.from('jobs').select('id, source_path').lt('created_at', d7).not('source_path', 'is', null).in('status', ['done', 'failed']).limit(200);
  for (const j of olds ?? []) {
    await r2del(`uploads/${j.source_path}`);
    await setJob(j.id, { source_path: null });
  }
  const { data: outs } = await sb.from('jobs').select('id, outputs').lt('finished_at', d90).eq('status', 'done').neq('outputs', '[]').limit(200);
  for (const j of outs ?? []) {
    for (const o of j.outputs) await r2del(`outputs/${o.path}`);
    await setJob(j.id, { outputs: [], error: 'Clips nach 90 Tagen gelöscht' });
  }
  // Rohmaterial, das für Änderungswünsche aufgehoben wurde, nach Ablauf der 24 Stunden löschen
  const { data: abg } = await sb.from('jobs').select('id, source_path, source_paths').lt('keep_until', new Date().toISOString()).eq('status', 'done').limit(200);
  for (const j of abg ?? []) {
    if (j.source_path) await r2del(`uploads/${j.source_path}`);
    for (const p of j.source_paths ?? []) await r2del(`uploads/${p}`);
    await setJob(j.id, { source_path: null, source_paths: [], keep_until: null });
  }
  if ((olds?.length || 0) + (outs?.length || 0)) log('🧹 aufgeräumt', olds?.length || 0, 'Uploads,', outs?.length || 0, 'Aufträge mit Clips');
}

// Hängengebliebene Aufträge dieser Maschine (z. B. nach Absturz) wieder freigeben
await sb.from('jobs').update({ status: 'queued', progress: 0 }).eq('status', 'processing').eq('worker', WORKER);
await heartbeat({ busy_job: null, last_output: null }).catch(() => null);
log(`Schnitt-Maschine „${WORKER}“ läuft. Warte auf Aufträge …`);
if (CLAUDE_SCHNITT) log(CLAUDE_AUTOSTART ? `Claude-Schnitt: startet selbst pro Auftrag (${CLAUDE_BIN}, höchstens ${CLAUDE_PARALLEL} gleichzeitig, Anleitung ${CLAUDE_ANLEITUNG || 'FEHLT'})` : 'Claude-Schnitt: über die geplante Aufgabe (CLAUDE_AUTOSTART=0)');
let lastCleanup = 0;
let lastFrist = 0;
let lastClaude = 0;
for (;;) {
  try {
    // Claude-Auslöser: liegengebliebene Aufträge nachholen (nach Neustart, nach gescheitertem Lauf) – liest nur den Ablage-Ordner
    if (Date.now() - lastClaude > 60e3) { claudeStarten(); lastClaude = Date.now(); }
    if (Date.now() - lastCleanup > 1 * 3600e3) { await cleanup(); await refreshTrends().catch(() => null); lastCleanup = Date.now(); }
    if (Date.now() - lastFrist > 2 * 60e3) { await claudeFristPruefen().catch((e) => log('Frist-Prüfung:', e.message)); lastFrist = Date.now(); }
    const { count } = await sb.from('jobs').select('id', { count: 'exact', head: true }).eq('status', 'queued');
    // Claude-Schnitt für alle: Die Maschine braucht für diese Aufträge keine KI von Google – der Wächter hält sie dann nicht auf
    // (Test 13.10.: Prepaid leer + Gratis-Limit erreicht → der Auftrag blieb in der Schlange, obwohl Claude ihn schneiden sollte).
    let claudeDran = CLAUDE_SCHNITT === 'alle';
    if (count && claudeDran) {   // Ausweich-Aufträge schneidet die Maschine selbst – dafür braucht sie die KI wie früher
      const { data: nx } = await sb.from('jobs').select('id').eq('status', 'queued').order('created_at', { ascending: true }).limit(1);
      if (nx?.[0] && ausweichListe().has(nx[0].id)) claudeDran = false;
    }
    if (count && CLAUDE_SCHNITT === 'lern') {   // der nächste Auftrag gehört einem Lernkonto → Claude schneidet, Google wird nicht gebraucht
      const { data: nx } = await sb.from('jobs').select('user_id').eq('status', 'queued').order('created_at', { ascending: true }).limit(1);
      claudeDran = !!nx?.[0] && await istLernkonto(nx[0].user_id).catch(() => false);
    }
    if (count && !claudeDran && !(await aiReady())) {
      await sb.from('jobs').update({ stage: 'Gerade viel los – startet automatisch, du bekommst eine E-Mail, sobald dein Video fertig ist' }).eq('status', 'queued');
      await new Promise((r) => setTimeout(r, 30_000));
      continue;
    }
    const { data, error } = await sb.rpc('claim_next_job', { p_worker: WORKER });
    if (error) log('Fehler beim Abholen:', error.message);
    const job = data?.[0];
    if (job) await processJob(job);
    else await new Promise((r) => setTimeout(r, POLL_MS));
  } catch (e) {
    log('Unerwarteter Fehler:', e.message);
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
}
