// Velox Cut – Schnitt-Maschine. Läuft auf einem eigenen Rechner (Beta: dein Mac), nicht auf Vercel.
// Start:  node --env-file=worker/.env worker/worker.mjs
// Holt Aufträge aus Supabase, schneidet sie, lädt die Clips hoch und meldet den Status.
import { createClient } from '@supabase/supabase-js';
import { AwsClient } from 'aws4fetch';
import { spawn } from 'node:child_process';
import { createWriteStream, existsSync } from 'node:fs';
import { mkdir, readFile, rm, stat } from 'node:fs/promises';
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
  if (!process.env.GEMINI_API_KEY) return true;
  if (Date.now() - ai.checked < (ai.ok ? 10 * 60e3 : 2 * 60e3)) return ai.ok;
  ai.checked = Date.now();
  let code = 0;
  try {
    const r = await fetch('https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent', {
      method: 'POST', signal: AbortSignal.timeout(30_000),
      headers: { 'x-goog-api-key': process.env.GEMINI_API_KEY, 'Content-Type': 'application/json' },
      body: JSON.stringify({ contents: [{ parts: [{ text: 'ok' }] }], generationConfig: { maxOutputTokens: 1 } }),
    });
    code = r.status;
  } catch { code = -1; }
  const was = ai.ok;
  ai.ok = code === 200 || code === 429 || code === 503; // ausgelastet/überlastet: die Pipeline wiederholt selbst und weicht aus
  if (!ai.ok && was) { ai.since = Date.now(); log(`KI nicht erreichbar (HTTP ${code}) – Aufträge warten`); }
  if (ai.ok && !was) {
    log('KI wieder erreichbar – es geht weiter');
    await mail({ art: 'warnung', betreff: 'KI wieder erreichbar', text: `Die KI antwortet wieder. Wartende Aufträge werden jetzt geschnitten (Ausfall ca. ${Math.round((Date.now() - ai.since) / 60e3)} Min.).` });
  }
  if (!ai.ok && Date.now() - ai.warned > 3 * 3600e3) {
    ai.warned = Date.now();
    const grund = code === 402 ? 'Das Guthaben bei Google (Gemini) ist aufgebraucht. Bitte in aistudio.google.com → Billing aufladen und Auto-Aufladen einschalten.'
      : code === 401 || code === 403 ? 'Der Gemini-Schlüssel wird abgelehnt (ungültig oder gesperrt).'
      : `Google antwortet nicht richtig (HTTP ${code}).`;
    await mail({ art: 'warnung', betreff: 'KI nicht erreichbar – Aufträge warten', text: `${grund}\n\nNeue Aufträge bleiben so lange in der Warteschlange und starten automatisch, sobald die KI wieder antwortet. Kunden sehen „startet gleich“.` });
  }
  return ai.ok;
}

/** Google-Drive-Freigabelink → direkter Download */
function driveDirect(url) {
  const id = /\/d\/([\w-]{20,})/.exec(url)?.[1] || new URL(url).searchParams.get('id');
  if (!id) throw new Error('Google-Drive-Link nicht erkannt.');
  return `https://drive.usercontent.google.com/download?id=${id}&export=download&confirm=t`;
}

async function download(job, dir) {
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
  if (job.source_kind === 'upload') {
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
    const args = [join(import.meta.dirname, 'montage.py'), '--out', join(dir, 'montage'), '--style', job.style, '--clips', String(job.clips), ...(words ? ['--words', words.slice(0, 300)] : []), inDir];
    const p = spawn(PYTHON, args, { stdio: ['ignore', 'pipe', 'pipe'], env: { VELOX_MASCHINE: MASCHINE, ...process.env, PYTHONUNBUFFERED: '1' } });
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
      if (code !== 0) return reject(new Error(errTxt.split('\n').filter(Boolean).pop() || `Montage-Fehler ${code}`));
      try { resolve(JSON.parse(last)); } catch { reject(new Error('Montage lieferte kein Ergebnis.')); }
    });
  });
}

function runPipeline(job, src, dir, logo, words = '', plan = null, clips = job.clips) {
  const args = [PIPELINE, '--src', src, '--out', dir, '--style', job.style, '--clips', String(clips), '--caption', job.caption_style, '--color', job.caption_color];
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
    const p = spawn(PYTHON, args, { stdio: ['ignore', 'pipe', 'pipe'], env: { VELOX_MASCHINE: MASCHINE, ...process.env, PYTHONUNBUFFERED: '1' } });
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
      if (code !== 0) return reject(new Error(errTxt.split('\n').filter(Boolean).pop() || `Pipeline-Fehler ${code}`));
      try { resolve(JSON.parse(last)); } catch { reject(new Error('Pipeline lieferte kein Ergebnis.')); }
    });
  });
}

async function processJob(job) {
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
        result = await runPipeline(job, src, dir, logo, pw?.brand_words || '', plan, plan ? 1 : job.clips); // Montage = eine Geschichte = ein Video
      } catch (e) { // einzelnes Video ohne Sprache (nur B-Roll) → Musik-Schnitt statt Fehlermeldung
        if (plan || !/keine Sprache|keine Tonspur/.test(String(e?.message))) throw e;
        result = await runMusikschnitt(job, src, dir, logo, pw?.brand_words || '');
      }
    }
    if (!result.clips?.length) throw new Error('Es konnten keine Clips erstellt werden.');
    const outputs = [];
    for (const [i, c] of result.clips.entries()) {
      const path = `${job.user_id}/${job.id}/clip-${i + 1}.mp4`;
      const up = await r2put(`outputs/${path}`, await readFile(c.file));
      if (!up.ok) throw new Error('Hochladen des Clips fehlgeschlagen.');
      outputs.push({ path, title: c.title || `Clip ${i + 1}`, seconds: c.seconds, songs: c.songs ?? [], post: c.post ?? null });
    }
    // weniger Clips als bezahlt → Differenz erstatten
    if (outputs.length < job.clips) {
      const per = Math.round(job.credits_charged / job.clips);
      await sb.rpc('refund_credits', { p_user: job.user_id, p_amount: per * (job.clips - outputs.length), p_ref: `${job.id}-teil` });
    }
    // nur abschließen, wenn der Wächter den Auftrag nicht schon beendet (und erstattet) hat
    const { data: fin } = await setJob(job.id, { status: 'done', progress: 100, outputs, finished_at: new Date().toISOString() }).eq('status', 'processing').select('id');
    if (!fin?.length) {
      log('⚠', job.id, 'war schon vom Wächter beendet – Clips verworfen');
      for (const o of outputs) await r2del(`outputs/${o.path}`);
      return;
    }
    // Rohvideo sofort löschen – wird nicht mehr gebraucht (Datenschutz, Speicherplatz)
    if (job.source_path) { await r2del(`uploads/${job.source_path}`); await setJob(job.id, { source_path: null }); }
    if (job.source_paths?.length) { for (const p of job.source_paths) await r2del(`uploads/${p}`); await setJob(job.id, { source_paths: [] }); }
    log('✓', job.id, `${outputs.length} Clip(s)`);
    await mail({ art: 'fertig', job: job.id }); // „Dein Video ist fertig“ – Kunde muss das Studio nicht offen lassen
  } catch (e) {
    log('✗', job.id, e.message);
    await setJob(job.id, { status: 'failed', error: friendly(String(e.message)).slice(0, 300), finished_at: new Date().toISOString() }).eq('status', 'processing');
    await sb.rpc('refund_job', { p_job: job.id });
  } finally {
    await heartbeat({ busy_job: null, last_output: null }).catch(() => null);
    await rm(dir, { recursive: true, force: true });
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
  if ((olds?.length || 0) + (outs?.length || 0)) log('🧹 aufgeräumt', olds?.length || 0, 'Uploads,', outs?.length || 0, 'Aufträge mit Clips');
}

// Hängengebliebene Aufträge dieser Maschine (z. B. nach Absturz) wieder freigeben
await sb.from('jobs').update({ status: 'queued', progress: 0 }).eq('status', 'processing').eq('worker', WORKER);
await heartbeat({ busy_job: null, last_output: null }).catch(() => null);
log(`Schnitt-Maschine „${WORKER}“ läuft. Warte auf Aufträge …`);
let lastCleanup = 0;
for (;;) {
  try {
    if (Date.now() - lastCleanup > 6 * 3600e3) { await cleanup(); await refreshTrends().catch(() => null); lastCleanup = Date.now(); }
    const { count } = await sb.from('jobs').select('id', { count: 'exact', head: true }).eq('status', 'queued');
    if (count && !(await aiReady())) {
      await sb.from('jobs').update({ stage: 'Startet gleich – kurze Verzögerung' }).eq('status', 'queued');
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
