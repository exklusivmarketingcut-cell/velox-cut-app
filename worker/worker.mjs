// Velox Cut – Schnitt-Maschine. Läuft auf einem eigenen Rechner (Beta: dein Mac), nicht auf Vercel.
// Start:  node --env-file=worker/.env worker/worker.mjs
// Holt Aufträge aus Supabase, schneidet sie, lädt die Clips hoch und meldet den Status.
import { createClient } from '@supabase/supabase-js';
import { AwsClient } from 'aws4fetch';
import { spawn } from 'node:child_process';
import { createWriteStream, existsSync, readFileSync, readdirSync } from 'node:fs';
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
// AUSWEICHEN (Launch-Vorbereitung 16.10.: „nichts darf hängen bleiben“). Wird ein Auftrag, der an den Claude-Schnitt gegangen
// ist, nicht innerhalb von CLAUDE_FRIST_MIN Minuten (Standard 45) angefasst – Claude-App zu, geplante Aufgabe nicht freigegeben,
// Rechner war aus –, schneidet die Maschine ihn selbst auf dem bewährten Weg (pipeline/montage) aus dem abgelegten Rohmaterial.
// Der Kunde bekommt also immer ein Video. Merkliste: schnitt-maschine/claude_ausweich.json.
const CLAUDE_FRIST_MIN = Number(process.env.CLAUDE_FRIST_MIN || 45);
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
async function claudeFristPruefen() {
  if (!CLAUDE_SCHNITT) return;
  const liste = ausweichListe();
  for (const { ordner, f, j } of claudeAblagen()) {
    if (j.erledigt || Date.now() - Date.parse(j.wartet_seit) < CLAUDE_FRIST_MIN * 60e3) continue;
    const ed = join(ordner, 'edit');   // Claude arbeitet gerade daran (in den letzten 20 Min. etwas geschrieben) → nicht dazwischenfunken
    if (existsSync(ed) && Date.now() - (await stat(ed)).mtimeMs < 20 * 60e3) continue;
    const { data: z } = await sb.from('jobs').update({ status: 'queued', stage: 'Dein Video wird gerade geschnitten …' }).eq('id', j.id).eq('status', 'review').select('id');
    j.erledigt = z?.length ? 'ausweich' : 'nicht-mehr-offen'; j.erledigt_am = new Date().toISOString();
    await writeFile(f, JSON.stringify(j, null, 1));
    if (!z?.length) continue;
    liste.add(j.id);
    await writeFile(AUSWEICH_DATEI, JSON.stringify([...liste]));
    log('⏱', j.id, `wartete über ${CLAUDE_FRIST_MIN} Min. auf den Claude-Schnitt – die Maschine schneidet ihn jetzt selbst`);
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
  return true;
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
    for (const p of job.source_paths || []) {
      const r = await r2get(`uploads/${p}`);
      if (!r.ok) throw new Error('Eine hochgeladene Datei fehlt.');
      await streamPipeline(Readable.fromWeb(r.body), createWriteStream(join(inDir, p.split('/').pop())));
    }
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
let lastCleanup = 0;
let lastFrist = 0;
for (;;) {
  try {
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
