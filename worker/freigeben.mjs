// Velox Cut – geprüfte Videos eines Lernkontos freigeben (Weg B, 10.10.2026).
//   node --env-file=worker/.env worker/freigeben.mjs                      → Liste der wartenden Aufträge
//   node --env-file=worker/.env worker/freigeben.mjs <Auftrag>            → so freigeben, wie geschnitten
//   node --env-file=worker/.env worker/freigeben.mjs <Auftrag> 1=/pfad/neu.mp4 [2=…]   → Clip 1 vorher ersetzen
//   node --env-file=worker/.env worker/freigeben.mjs <Auftrag> --ablehnen → fehlgeschlagen + Credits zurück
import { createClient } from '@supabase/supabase-js';
import { AwsClient } from 'aws4fetch';
import { existsSync, readdirSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { join } from 'node:path';

const sb = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_ROLE_KEY, { auth: { persistSession: false } });
const MASCHINE = process.env.VELOX_MASCHINE || join(import.meta.dirname, '..', '..', 'schnitt-maschine');
const LERN = join(MASCHINE, 'lern');
const r2 = new AwsClient({ accessKeyId: process.env.R2_ACCESS_KEY_ID, secretAccessKey: process.env.R2_SECRET_ACCESS_KEY, service: 's3', region: 'auto' });
const R2_BASE = `${process.env.R2_ENDPOINT || `https://${process.env.R2_ACCOUNT_ID}.r2.cloudflarestorage.com`}/${process.env.R2_BUCKET}`;
const r2put = (key, body) => r2.fetch(`${R2_BASE}/${key.split('/').map(encodeURIComponent).join('/')}`, { method: 'PUT', body, headers: { 'Content-Type': 'video/mp4' } });
const APP_URL = process.env.APP_URL || 'https://velox-cut.de';

const offen = () => (existsSync(LERN) ? readdirSync(LERN) : []).filter((d) => existsSync(join(LERN, d, 'freigabe.json')))
  .map((d) => ({ dir: join(LERN, d), ...JSON.parse(readFileSync(join(LERN, d, 'freigabe.json'), 'utf8')) })).filter((f) => !f.erledigt);

const [id, ...rest] = process.argv.slice(2);

// MEHRERE KUNDEN GLEICHZEITIG (Inhaber 18.10.): Jeder Lauf nimmt sich mit --nehmen <Auftrag> genau EINEN Auftrag. Die Markierung
// (ORDNER/claude_in_arbeit.txt) wird exklusiv angelegt – zwei Läufe können nie denselben Auftrag bearbeiten (Test 17./18.10.: drei
// Läufe am selben Auftrag überschrieben sich den Schnittplan). Verfällt nach 90 Min. (abgestürzter Lauf). Freigeben/Ablehnen räumt
// sie weg, --loslassen <Auftrag> gibt einen Auftrag zurück. --start/--ende bleiben als harmlose Befehle (alte Anleitung).
const BELEGT_MIN = 90;
const markierung = (dir) => join(dir, 'claude_in_arbeit.txt');
const belegt = (dir) => { try { return Date.now() - Date.parse(readFileSync(markierung(dir), 'utf8')) < BELEGT_MIN * 60e3; } catch { return false; } };
if (id === '--start' || id === '--ende') { console.log('ok'); process.exit(0); }
if (id === '--nehmen' || id === '--loslassen') {
  const f = offen().find((x) => x.id === rest[0] || x.id.startsWith(rest[0] || '-'));
  if (!f) { console.log('Kein offener Auftrag mit dieser Nummer.'); process.exit(0); }
  const m = markierung(f.dir);
  if (id === '--loslassen') { try { unlinkSync(m); } catch { /* schon frei */ } console.log('Auftrag zurückgegeben.'); process.exit(0); }
  if (belegt(f.dir)) { console.log('BELEGT – ein anderer Lauf arbeitet an diesem Auftrag. Nimm den nächsten.'); process.exit(0); }
  try { unlinkSync(m); } catch { /* keine alte */ }
  try { writeFileSync(m, new Date().toISOString(), {flag: 'wx'}); } catch { console.log('BELEGT – ein anderer Lauf war schneller. Nimm den nächsten.'); process.exit(0); }
  console.log(`GENOMMEN – dieser Lauf bearbeitet ${f.id}. Ordner: ${f.dir}`);
  process.exit(0);
}
// REIHENFOLGE (Inhaber 17.10.: niemand soll unnötig warten): Clean und Viral sind in ~5 Min. gebaut, Premium braucht ~20 Min.
// Schnelle Aufträge kommen deshalb zuerst – ein Premium-Auftrag, der schon 40 Min. wartet, rückt aber wieder nach vorn.
const minuten = (f) => (Date.now() - Date.parse(f.wartet_seit)) / 60e3;
const rang = (f) => (f.claude && f.stil === 'premium' && minuten(f) < 40 ? 1 : 0);
if (!id) {
  const l = offen().sort((a, b) => rang(a) - rang(b) || Date.parse(a.wartet_seit) - Date.parse(b.wartet_seit));
  console.log(l.length ? l.map((f) => `${f.id}  wartet seit ${f.wartet_seit}  ${f.claude && belegt(f.dir) ? 'IN ARBEIT bei einem anderen Lauf – überspringen' : f.claude ? `CLAUDE-SCHNITT (noch nicht geschnitten, Stil ${f.stil || '?'}${existsSync(join(f.dir, 'edit', 'material.md')) ? ', schon vorgehört' : ''})` : `${f.outputs.length} Clip(s) zur Prüfung`}  ${f.dir}`).join('\n') : 'Nichts wartet auf Prüfung.');
  process.exit(0);
}
const f = offen().find((x) => x.id === id || x.id.startsWith(id));
if (!f) { console.error('Kein wartender Auftrag mit dieser Nummer.'); process.exit(1); }
const fertig = (wie) => { writeFileSync(join(f.dir, 'freigabe.json'), JSON.stringify({ ...f, dir: undefined, erledigt: wie, erledigt_am: new Date().toISOString() }, null, 1)); try { unlinkSync(markierung(f.dir)); } catch { /* keine */ } };

if (rest.includes('--ablehnen')) {
  const { data } = await sb.from('jobs').update({ status: 'failed', finished_at: new Date().toISOString(), error: 'Dieses Video konnten wir nicht in guter Qualität schneiden. Bitte versuche es mit einer anderen Aufnahme' }).eq('id', f.id).eq('status', 'review').select('id');
  if (!data?.length) { console.error('Auftrag steht nicht (mehr) auf Prüfung.'); process.exit(1); }
  await sb.rpc('refund_job', { p_job: f.id });
  fertig('abgelehnt');
  console.log('Abgelehnt, Credits erstattet.');
  process.exit(0);
}
for (const r of rest) {
  const m = /^(\d+)=(.+)$/.exec(r);
  if (!m) { console.error('Unbekannte Angabe:', r); process.exit(1); }
  const o = f.outputs[+m[1] - 1];
  if (!o || !existsSync(m[2])) { console.error('Clip oder Datei fehlt:', r); process.exit(1); }
  const up = await r2put(`outputs/${o.path}`, await readFile(m[2]));
  if (!up.ok) { console.error('Hochladen fehlgeschlagen:', up.status); process.exit(1); }
  const d = spawnSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', m[2]], { encoding: 'utf8' });
  if (+d.stdout > 0) o.seconds = Math.round(+d.stdout);
  console.log(`Clip ${m[1]} ersetzt.`);
}
// Claude-Schnitt: Titel und Post-Text (mit Hashtags) stehen im Schnittplan – der Kunde sieht sie im Studio neben dem Video
if (f.claude) {
  try {
    const plan = JSON.parse(readFileSync(join(f.dir, 'edit', 'plan.json'), 'utf8'));
    if (plan.titel) f.outputs[0].title = String(plan.titel).slice(0, 80);
    if (plan.post?.text) f.outputs[0].post = { text: String(plan.post.text).slice(0, 600), hashtags: (plan.post.hashtags || []).slice(0, 8).map(String) };
  } catch { /* ohne Plan bleibt der Dateiname als Titel */ }
  if (!f.outputs[0].seconds) { console.error('Kein fertiges Video angegeben (1=/pfad/final.mp4).'); process.exit(1); }
}
const { data, error } = await sb.from('jobs').update({ status: 'done', progress: 100, outputs: f.outputs, finished_at: new Date().toISOString() }).eq('id', f.id).eq('status', 'review').select('id');
if (error || !data?.length) { console.error('Freigabe nicht möglich:', error?.message || 'Auftrag steht nicht (mehr) auf Prüfung.'); process.exit(1); }
await fetch(`${APP_URL}/api/intern/mail`, { method: 'POST', body: JSON.stringify({ art: 'fertig', job: f.id }), headers: { Authorization: `Bearer ${process.env.SUPABASE_SERVICE_ROLE_KEY}`, 'Content-Type': 'application/json' } }).catch(() => null);
fertig('freigegeben');
// Claude-Schnitt für normale Kunden: Das Rohmaterial wird nach der Auslieferung nicht aufgehoben (Datenschutz: Uploads werden nach
// dem Schnitt gelöscht). Beim Lernkonto bleibt es als Lernmaterial liegen, wie vereinbart.
if (f.claude && !f.lernkonto) {
  const { rm } = await import('node:fs/promises');
  for (const d of readdirSync(f.dir)) if (!['auftrag.json', 'freigabe.json'].includes(d)) await rm(join(f.dir, d), { recursive: true, force: true });
  console.log('Rohmaterial des Kunden gelöscht.');
}
console.log('Freigegeben – der Kunde sieht das Video jetzt und bekommt die E-Mail.');
