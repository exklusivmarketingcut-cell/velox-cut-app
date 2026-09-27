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
// Python mit mlx-whisper + OpenCV (eigene Umgebung auf der externen SSD, siehe SETUP.md)
const PYTHON = process.env.PYTHON || '/Volumes/PortableSSD/velox-worker/venv/bin/python';
// Zwischendateien auf die externe SSD (die interne Festplatte ist fast voll), sonst ins System-Temp
const TMP = process.env.WORKER_TMP || (existsSync('/Volumes/PortableSSD') ? '/Volumes/PortableSSD/velox-worker-tmp' : join(tmpdir(), 'velox-worker'));
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

/** Google-Drive-Freigabelink → direkter Download */
function driveDirect(url) {
  const id = /\/d\/([\w-]{20,})/.exec(url)?.[1] || new URL(url).searchParams.get('id');
  if (!id) throw new Error('Google-Drive-Link nicht erkannt.');
  return `https://drive.usercontent.google.com/download?id=${id}&export=download&confirm=t`;
}

async function download(job, dir) {
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

/** Pipeline starten (Python). Meldet Fortschritt als Zeilen „PROGRESS 42“, Ergebnis als letzte JSON-Zeile. */
function runPipeline(job, src, dir) {
  return new Promise((resolve, reject) => {
    const args = [PIPELINE, '--src', src, '--out', dir, '--style', job.style, '--clips', String(job.clips), '--caption', job.caption_style, '--color', job.caption_color];
    const p = spawn(PYTHON, args, { stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env, PYTHONUNBUFFERED: '1' } });
    let last = '';
    let errTxt = '';
    p.stdout.on('data', (b) => {
      for (const line of String(b).split('\n')) {
        const m = /^PROGRESS (\d+)/.exec(line);
        const st = /^STAGE (.+)$/.exec(line);
        if (m) setJob(job.id, { progress: Math.min(99, +m[1]) });
        else if (st) setStage(job.id, st[1].trim());
        else if (line.trim()) last = line.trim();
      }
    });
    p.stderr.on('data', (b) => (errTxt = (errTxt + b).slice(-2000)));
    p.on('close', (code) => {
      if (code !== 0) return reject(new Error(errTxt.split('\n').filter(Boolean).pop() || `Pipeline-Fehler ${code}`));
      try { resolve(JSON.parse(last)); } catch { reject(new Error('Pipeline lieferte kein Ergebnis.')); }
    });
  });
}

async function processJob(job) {
  const dir = join(TMP, job.id);
  await mkdir(dir, { recursive: true });
  log('▶', job.id, job.style, `${job.clips} Clip(s)`, job.source_name ?? '');
  try {
    await setStage(job.id, 'Lädt dein Video …');
    const src = await download(job, dir);
    await setJob(job.id, { progress: 10 });
    const result = await runPipeline(job, src, dir); // { clips: [{file, title, seconds}] }
    if (!result.clips?.length) throw new Error('Es konnten keine Clips erstellt werden.');
    const outputs = [];
    for (const [i, c] of result.clips.entries()) {
      const path = `${job.user_id}/${job.id}/clip-${i + 1}.mp4`;
      const up = await r2put(`outputs/${path}`, await readFile(c.file));
      if (!up.ok) throw new Error('Hochladen des Clips fehlgeschlagen.');
      outputs.push({ path, title: c.title || `Clip ${i + 1}`, seconds: c.seconds });
    }
    // weniger Clips als bezahlt → Differenz erstatten
    if (outputs.length < job.clips) {
      const per = Math.round(job.credits_charged / job.clips);
      await sb.rpc('refund_credits', { p_user: job.user_id, p_amount: per * (job.clips - outputs.length), p_ref: `${job.id}-teil` });
    }
    await setJob(job.id, { status: 'done', progress: 100, outputs, finished_at: new Date().toISOString() });
    // Rohvideo sofort löschen – wird nicht mehr gebraucht (Datenschutz, Speicherplatz)
    if (job.source_path) { await r2del(`uploads/${job.source_path}`); await setJob(job.id, { source_path: null }); }
    log('✓', job.id, `${outputs.length} Clip(s)`);
  } catch (e) {
    log('✗', job.id, e.message);
    await setJob(job.id, { status: 'failed', error: String(e.message).slice(0, 300), finished_at: new Date().toISOString() });
    await sb.rpc('refund_job', { p_job: job.id });
  } finally {
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
log(`Schnitt-Maschine „${WORKER}“ läuft. Warte auf Aufträge …`);
let lastCleanup = 0;
for (;;) {
  try {
    if (Date.now() - lastCleanup > 6 * 3600e3) { await cleanup(); lastCleanup = Date.now(); }
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
