// Velox Cut – DEMO-Backend. Bildet Supabase (Login, Datenbank, Speicher) und Stripe (Testmodus) lokal nach,
// damit die echte App ohne Konten und ohne echtes Geld durchgeklickt werden kann. NUR für lokale Vorführungen.
// Start:  node demo/backend.mjs      (Supabase auf :54321, Stripe auf :12111)
// Alle Daten liegen nur im Arbeitsspeicher – nach einem Neustart ist alles leer.
import http from 'node:http';
import crypto from 'node:crypto';
import { mkdirSync, readFileSync } from 'node:fs';
import { readFile, writeFile, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { tmpdir } from 'node:os';

const env = Object.fromEntries(
  readFileSync(join(import.meta.dirname, '..', '.env.local'), 'utf8')
    .split('\n').filter((l) => /^\w+=/.test(l)).map((l) => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1).trim()]),
);
const SERVICE_KEY = env.SUPABASE_SERVICE_ROLE_KEY;
const WEBHOOK_SECRET = env.STRIPE_WEBHOOK_SECRET;
const APP = env.DEMO_APP_URL || 'http://localhost:3010';
const SB_PORT = 54321;
const ST_PORT = 12111;
const FILES = join(tmpdir(), 'velox-demo-files');
await rm(FILES, { recursive: true, force: true });
mkdirSync(FILES, { recursive: true });

const log = (...a) => console.log(new Date().toLocaleTimeString('de-DE'), ...a);
const now = () => new Date().toISOString();
const uuid = () => crypto.randomUUID();
const rid = (p) => `${p}_${crypto.randomBytes(12).toString('hex')}`;
const days = (n) => new Date(Date.now() + n * 864e5).toISOString();

// ───────────────────────────── Datenbank (wie supabase/migrations/0001_init.sql) ─────────────────────────────
const db = { profiles: [], credit_ledger: [], jobs: [], beta_requests: [], cancellations: [], stripe_events: [] };
const users = new Map(); // email → {id, email, password, meta}
let serial = 1;

const DEFAULTS = {
  profiles: () => ({ name: null, stripe_customer_id: null, default_payment_method: null, card_on_file: false, plan: 'free', plan_interval: null,
    plan_status: null, subscription_id: null, plan_credits: 10, topup_credits: 0, free_refill_at: days(30), plan_credits_reset_at: null, created_at: now() }),
  credit_ledger: () => ({ id: serial++, ref: null, created_at: now() }),
  jobs: () => ({ id: uuid(), status: 'queued', caption_style: 'rise', caption_color: '#F6E27F', clips: 1, source_path: null, source_url: null, source_name: null,
    progress: 0, outputs: [], error: null, worker: null, created_at: now(), started_at: null, finished_at: null }),
  beta_requests: () => ({ id: serial++, created_at: now() }),
  cancellations: () => ({ id: serial++, created_at: now() }),
  stripe_events: () => ({ created_at: now() }),
};
const PK = { profiles: 'id', jobs: 'id', stripe_events: 'id' };
class Conflict extends Error {}

function insert(table, row) {
  const r = { ...DEFAULTS[table](), ...row };
  const pk = PK[table];
  if (pk && db[table].some((x) => x[pk] === r[pk])) throw new Conflict(`duplicate key value violates unique constraint "${table}_pkey"`);
  // Jede Stripe-Zahlung höchstens einmal gutschreiben (credit_ledger_ref_once)
  if (table === 'credit_ledger' && ['topup', 'plan_period'].includes(r.reason) && db.credit_ledger.some((x) => x.ref === r.ref && x.reason === r.reason))
    throw new Conflict('duplicate key value violates unique constraint "credit_ledger_ref_once"');
  if (table === 'profiles' && (r.plan_credits < 0 || r.topup_credits < 0)) throw new Error('check constraint');
  db[table].push(r);
  return r;
}
const profile = (id) => db.profiles.find((p) => p.id === id);
const ledger = (user_id, delta, bucket, reason, ref = null) => insert('credit_ledger', { user_id, delta, bucket, reason, ref });

// Datenbank-Funktionen (gleiche Logik wie die SQL-Funktionen)
const RPC = {
  spend_credits({ p_user, p_amount, p_reason, p_ref }) {
    const p = profile(p_user);
    if (!p) return false;
    if (p.plan === 'free' && p.free_refill_at <= now()) {
      ledger(p_user, 10 - p.plan_credits, 'plan', 'free_refill');
      Object.assign(p, { plan_credits: 10, free_refill_at: days(30) });
    }
    if (p.plan_credits + p.topup_credits < p_amount) return false;
    const fromPlan = Math.min(p.plan_credits, p_amount);
    const fromTopup = p_amount - fromPlan;
    p.plan_credits -= fromPlan;
    p.topup_credits -= fromTopup;
    if (fromPlan > 0) ledger(p_user, -fromPlan, 'plan', p_reason, p_ref);
    if (fromTopup > 0) ledger(p_user, -fromTopup, 'topup', p_reason, p_ref);
    return true;
  },
  grant_topup({ p_user, p_amount, p_ref }) {
    try { ledger(p_user, p_amount, 'topup', 'topup', p_ref); } catch (e) { if (e instanceof Conflict) return false; throw e; }
    profile(p_user).topup_credits += p_amount;
    return true;
  },
  reset_plan_credits({ p_user, p_amount, p_ref }) {
    const p = profile(p_user);
    try { ledger(p_user, p_amount - p.plan_credits, 'plan', 'plan_period', p_ref); } catch (e) { if (e instanceof Conflict) return false; throw e; }
    Object.assign(p, { plan_credits: p_amount, plan_credits_reset_at: now() });
    return true;
  },
  refresh_credits({ p_user, p_plan_amount }) {
    const p = profile(p_user);
    if (!p) return null;
    if (p.plan === 'free' && p.free_refill_at <= now()) {
      ledger(p_user, 10 - p.plan_credits, 'plan', 'free_refill');
      Object.assign(p, { plan_credits: 10, free_refill_at: days(30) });
    } else if (p.plan_interval === 'yearly' && ['active', 'trialing'].includes(p.plan_status) && p.plan_credits_reset_at && p.plan_credits_reset_at <= days(-30)) {
      ledger(p_user, p_plan_amount - p.plan_credits, 'plan', 'yearly_month');
      Object.assign(p, { plan_credits: p_plan_amount, plan_credits_reset_at: now() });
    }
    return null;
  },
  downgrade_to_free({ p_user }) {
    const p = profile(p_user);
    ledger(p_user, 10 - p.plan_credits, 'plan', 'downgrade');
    Object.assign(p, { plan: 'free', plan_interval: null, plan_status: 'canceled', subscription_id: null, plan_credits: 10, free_refill_at: days(30) });
    return null;
  },
  refund_job({ p_job }) {
    const j = db.jobs.find((x) => x.id === p_job);
    if (!j || j.credits_charged <= 0) return false;
    ledger(j.user_id, j.credits_charged, 'topup', 'refund', p_job);
    profile(j.user_id).topup_credits += j.credits_charged;
    j.credits_charged = 0;
    return true;
  },
  refund_credits({ p_user, p_amount, p_ref }) {
    ledger(p_user, p_amount, 'topup', 'refund', p_ref);
    profile(p_user).topup_credits += p_amount;
    return true;
  },
  claim_next_job({ p_worker }) {
    const j = db.jobs.filter((x) => x.status === 'queued').sort((a, b) => a.created_at.localeCompare(b.created_at))[0];
    if (!j) return [];
    Object.assign(j, { status: 'processing', worker: p_worker, started_at: now(), progress: 1 });
    return [j];
  },
};

// ───────────────────────────── PostgREST-Nachbildung ─────────────────────────────
function parseVal(v) {
  if (v === 'null') return null;
  if (v === 'true') return true;
  if (v === 'false') return false;
  return v;
}
function matcher(col, expr) {
  let neg = false;
  if (expr.startsWith('not.')) { neg = true; expr = expr.slice(4); }
  const i = expr.indexOf('.');
  const op = expr.slice(0, i);
  const raw = expr.slice(i + 1);
  const cmp = (a, b) => (typeof a === 'number' ? a - Number(b) : String(a ?? '').localeCompare(String(b)));
  const f = {
    eq: (x) => String(x[col]) === raw,
    neq: (x) => (typeof x[col] === 'object' ? JSON.stringify(x[col]) !== raw : String(x[col]) !== raw),
    lt: (x) => x[col] != null && cmp(x[col], raw) < 0,
    lte: (x) => x[col] != null && cmp(x[col], raw) <= 0,
    gt: (x) => x[col] != null && cmp(x[col], raw) > 0,
    gte: (x) => x[col] != null && cmp(x[col], raw) >= 0,
    is: (x) => x[col] === parseVal(raw),
    in: (x) => raw.replace(/^\(|\)$/g, '').split(',').map((s) => s.replace(/^"|"$/g, '')).includes(String(x[col])),
    ilike: (x) => new RegExp(`^${raw.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/[%*]/g, '.*')}$`, 'i').test(String(x[col] ?? '')),
  }[op];
  if (!f) throw new Error(`Filter ${op} nicht nachgebildet`);
  return neg ? (x) => !f(x) : f;
}

async function rest(req, res, url, role, uid) {
  const [, , , table] = url.pathname.split('/'); // /rest/v1/<table>
  const body = ['POST', 'PATCH'].includes(req.method) ? JSON.parse((await readBody(req)).toString() || 'null') : null;

  if (table === 'rpc') {
    const fn = url.pathname.split('/')[4];
    if (role !== 'service') return json(res, 404, { code: 'PGRST202', message: 'Funktion nicht erlaubt' });
    try { return json(res, 200, RPC[fn](body ?? {})); } catch (e) { return json(res, 400, { code: 'P0001', message: e.message }); }
  }
  if (!db[table]) return json(res, 404, { code: '42P01', message: `Tabelle ${table} fehlt` });

  const filters = [];
  let order = null; let limit = Infinity; let select = '*';
  for (const [k, v] of url.searchParams) {
    if (k === 'select') select = v;
    else if (k === 'order') order = v;
    else if (k === 'limit') limit = Number(v);
    else if (k !== 'offset' && k !== 'columns' && k !== 'on_conflict') filters.push(matcher(k, v));
  }
  // Zugriffsschutz wie RLS: Nutzer lesen nur eigene Zeilen, schreiben gar nicht
  if (role !== 'service') {
    if (req.method !== 'GET') return json(res, 403, { code: '42501', message: 'permission denied' });
    const own = { profiles: 'id', credit_ledger: 'user_id', jobs: 'user_id' }[table];
    if (!own || !uid) return json(res, 200, []);
    filters.push((x) => x[own] === uid);
  }
  let rows = db[table].filter((x) => filters.every((f) => f(x)));
  const pick = (r) => (select === '*' ? { ...r } : Object.fromEntries(select.split(',').map((c) => c.trim()).map((c) => [c, r[c]])));
  const wantsObject = (req.headers.accept || '').includes('vnd.pgrst.object');
  const returning = (req.headers.prefer || '').includes('return=representation');

  const out = (list, status = 200) => {
    if (wantsObject) {
      if (list.length !== 1) return json(res, 406, { code: 'PGRST116', message: 'JSON object requested, multiple (or no) rows returned', details: `The result contains ${list.length} rows` });
      return json(res, status, pick(list[0]));
    }
    if (req.method !== 'GET' && !returning) return send(res, status === 200 ? 204 : status);
    return json(res, status, list.map(pick));
  };

  if (req.method === 'GET') {
    if (order) {
      const [col, dir] = order.split('.');
      rows = [...rows].sort((a, b) => (a[col] < b[col] ? -1 : a[col] > b[col] ? 1 : 0) * (dir === 'desc' ? -1 : 1));
    }
    return out(rows.slice(0, limit));
  }
  if (req.method === 'POST') {
    try {
      const added = (Array.isArray(body) ? body : [body]).map((r) => insert(table, r));
      return out(added, 201);
    } catch (e) {
      if (e instanceof Conflict) return json(res, 409, { code: '23505', message: e.message });
      return json(res, 400, { code: '23514', message: e.message });
    }
  }
  if (req.method === 'PATCH') {
    rows.forEach((r) => Object.assign(r, body));
    return out(rows);
  }
  if (req.method === 'DELETE') {
    db[table] = db[table].filter((x) => !rows.includes(x));
    return out(rows);
  }
  return json(res, 405, { message: 'Methode nicht erlaubt' });
}

// ───────────────────────────── Auth (GoTrue-Nachbildung) ─────────────────────────────
const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64url');
const refresh = new Map(); // refresh_token → user id
function session(u) {
  const exp = Math.floor(Date.now() / 1000) + 24 * 3600;
  const payload = { sub: u.id, email: u.email, role: 'authenticated', aud: 'authenticated', exp, iat: exp - 24 * 3600 };
  const token = `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64(payload)}.${crypto.createHmac('sha256', 'demo').update(b64(payload)).digest('base64url')}`;
  const rt = crypto.randomBytes(16).toString('hex');
  refresh.set(rt, u.id);
  return { access_token: token, token_type: 'bearer', expires_in: 24 * 3600, expires_at: exp, refresh_token: rt, user: publicUser(u) };
}
function publicUser(u) {
  return { id: u.id, aud: 'authenticated', role: 'authenticated', email: u.email, email_confirmed_at: u.created_at, confirmed_at: u.created_at,
    app_metadata: { provider: 'email', providers: ['email'] }, user_metadata: u.meta, identities: [], created_at: u.created_at, updated_at: u.created_at };
}
function userFromToken(token) {
  const parts = String(token || '').split('.');
  if (parts.length !== 3) return null;
  if (crypto.createHmac('sha256', 'demo').update(parts[1]).digest('base64url') !== parts[2]) return null;
  const p = JSON.parse(Buffer.from(parts[1], 'base64url').toString());
  if (p.exp * 1000 < Date.now()) return null;
  return [...users.values()].find((u) => u.id === p.sub) ?? null;
}
const authErr = (res, status, code, msg) => json(res, status, { code: status, error_code: code, msg, error: code, error_description: msg });

async function auth(req, res, url) {
  const path = url.pathname.replace('/auth/v1', '');
  const body = req.method === 'POST' || req.method === 'PUT' ? JSON.parse((await readBody(req)).toString() || '{}') : {};
  if (path === '/signup' && req.method === 'POST') {
    const email = String(body.email || '').toLowerCase();
    if (users.has(email)) return authErr(res, 422, 'user_already_exists', 'User already registered');
    if (String(body.password || '').length < 6) return authErr(res, 422, 'weak_password', 'Password should be at least 6 characters.');
    const u = { id: uuid(), email, password: body.password, meta: body.data ?? {}, created_at: now() };
    users.set(email, u);
    // Trigger handle_new_user: Profil + 10 Willkommens-Credits
    insert('profiles', { id: u.id, email, name: u.meta.name ?? null });
    ledger(u.id, 10, 'plan', 'signup');
    log('👤 neues Konto', email, '(E-Mail-Bestätigung im Demo übersprungen)');
    return json(res, 200, session(u));
  }
  if (path === '/token') {
    const grant = url.searchParams.get('grant_type');
    if (grant === 'password') {
      const u = users.get(String(body.email || '').toLowerCase());
      if (!u || u.password !== body.password) return authErr(res, 400, 'invalid_credentials', 'Invalid login credentials');
      log('🔑 Login', u.email);
      return json(res, 200, session(u));
    }
    if (grant === 'refresh_token') {
      const id = refresh.get(body.refresh_token);
      const u = [...users.values()].find((x) => x.id === id);
      if (!u) return authErr(res, 400, 'refresh_token_not_found', 'Invalid Refresh Token');
      return json(res, 200, session(u));
    }
  }
  if (path === '/user' && req.method === 'GET') {
    const u = userFromToken((req.headers.authorization || '').replace(/^Bearer /i, ''));
    return u ? json(res, 200, publicUser(u)) : authErr(res, 401, 'bad_jwt', 'invalid JWT');
  }
  if (path === '/logout') return send(res, 204);
  if (path === '/recover') { log('✉️  (Demo) Passwort-Link an', body.email); return json(res, 200, {}); }
  if (path === '/settings') return json(res, 200, { external: { email: true }, disable_signup: false, mailer_autoconfirm: true });
  return authErr(res, 404, 'not_found', 'Nicht nachgebildet: ' + path);
}

// ───────────────────────────── Speicher (Storage-Nachbildung) ─────────────────────────────
const uploadTokens = new Map(); // token → bucket/path
const fileOf = (key) => join(FILES, key);
async function storage(req, res, url, role) {
  const p = decodeURIComponent(url.pathname.replace('/storage/v1', ''));
  let m;
  if ((m = /^\/object\/upload\/sign\/(.+)$/.exec(p))) {
    if (req.method === 'POST') {
      if (role !== 'service') return json(res, 403, { message: 'permission denied' });
      const token = crypto.randomBytes(16).toString('hex');
      uploadTokens.set(token, m[1]);
      return json(res, 200, { url: `/object/upload/sign/${m[1]}?token=${token}` });
    }
    if (req.method === 'PUT') {
      const key = uploadTokens.get(url.searchParams.get('token'));
      if (key !== m[1]) return json(res, 400, { message: 'Upload-Link ungültig' });
      uploadTokens.delete(url.searchParams.get('token'));
      const raw = await readBody(req);
      const type = req.headers['content-type'] || '';
      let data = raw;
      if (type.startsWith('multipart/form-data')) {
        const fd = await new Response(raw, { headers: { 'content-type': type } }).formData();
        const f = [...fd.values()].find((v) => typeof v !== 'string');
        data = Buffer.from(await f.arrayBuffer());
      }
      mkdirSync(dirname(fileOf(key)), { recursive: true });
      await writeFile(fileOf(key), data);
      log('📥 Upload gespeichert', key, `${(data.length / 1048576).toFixed(1)} MB`);
      return json(res, 200, { Key: key });
    }
  }
  if ((m = /^\/object\/sign\/(.+)$/.exec(p))) {
    if (req.method === 'POST') {
      if (role !== 'service') return json(res, 403, { message: 'permission denied' });
      const token = crypto.createHmac('sha256', 'demo').update(m[1]).digest('hex');
      return json(res, 200, { signedURL: `/object/sign/${m[1]}?token=${token}` });
    }
    if (req.method === 'GET') {
      if (url.searchParams.get('token') !== crypto.createHmac('sha256', 'demo').update(m[1]).digest('hex')) return json(res, 400, { message: 'Link ungültig' });
      const dl = url.searchParams.get('download');
      return sendFile(req, res, fileOf(m[1]), dl ? { 'Content-Disposition': `attachment; filename="${dl}"` } : {});
    }
  }
  if ((m = /^\/object\/([^/]+)$/.exec(p)) && req.method === 'DELETE') {
    const { prefixes = [] } = JSON.parse((await readBody(req)).toString() || '{}');
    for (const x of prefixes) await rm(fileOf(`${m[1]}/${x}`), { force: true });
    return json(res, 200, prefixes.map((name) => ({ name })));
  }
  if ((m = /^\/object\/(.+)$/.exec(p))) {
    if (role !== 'service') return json(res, 403, { message: 'permission denied' });
    if (req.method === 'GET') return sendFile(req, res, fileOf(m[1]));
    if (req.method === 'POST' || req.method === 'PUT') {
      const data = await readBody(req);
      mkdirSync(dirname(fileOf(m[1])), { recursive: true });
      await writeFile(fileOf(m[1]), data);
      log('📤 Clip gespeichert', m[1], `${(data.length / 1048576).toFixed(1)} MB`);
      return json(res, 200, { Key: m[1] });
    }
  }
  return json(res, 404, { message: 'Nicht nachgebildet: ' + p });
}

// ───────────────────────────── Stripe (Testmodus-Nachbildung) ─────────────────────────────
const S = { customers: new Map(), setup_intents: new Map(), payment_methods: new Map(), subscriptions: new Map(), invoices: new Map(), payment_intents: new Map(), payments: [] };
const PRICES = [
  ['velox_starter_monthly', 1490, 'month'], ['velox_starter_yearly', 14900, 'year'], ['velox_pro_monthly', 2990, 'month'], ['velox_pro_yearly', 29900, 'year'],
  ['velox_business_monthly', 5990, 'month'], ['velox_business_yearly', 59900, 'year'], ['velox_p100', 990], ['velox_p300', 2790], ['velox_p600', 4990],
].map(([key, amount, iv]) => ({ id: `price_${key}`, object: 'price', active: true, currency: 'eur', lookup_key: key, unit_amount: amount,
  type: iv ? 'recurring' : 'one_time', recurring: iv ? { interval: iv, interval_count: 1 } : null, product: `prod_${key}` }));
const priceById = (id) => PRICES.find((p) => p.id === id);
const ts = () => Math.floor(Date.now() / 1000);
const stripeErr = (res, status, message, code = 'resource_missing', type = 'invalid_request_error') => json(res, status, { error: { type, code, message } });

/** Stripe-Formular-Body (a[b][0][c]=x) in ein Objekt umwandeln */
function parseForm(str) {
  const out = {};
  for (const [k, v] of new URLSearchParams(str)) {
    const keys = k.replace(/\]/g, '').split('[');
    let o = out;
    keys.forEach((key, i) => {
      if (i === keys.length - 1) o[key] = v;
      else o = o[key] ??= /^\d+$/.test(keys[i + 1]) ? [] : {};
    });
  }
  return out;
}

/** Webhook an die App schicken – signiert wie bei Stripe */
async function webhook(type, object) {
  const event = { id: rid('evt'), object: 'event', type, created: ts(), livemode: false, data: { object: structuredClone(object) } };
  const payload = JSON.stringify(event);
  const t = ts();
  const sig = crypto.createHmac('sha256', WEBHOOK_SECRET).update(`${t}.${payload}`).digest('hex');
  await new Promise((r) => setTimeout(r, 400));
  try {
    const r = await fetch(`${APP}/api/stripe/webhook`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Stripe-Signature': `t=${t},v1=${sig}` }, body: payload });
    log('🔔 Webhook', type, '→', r.status);
  } catch (e) {
    log('🔔 Webhook', type, 'fehlgeschlagen:', e.message);
  }
}

const cardOk = (pm) => pm && !['0002', '9995'].includes(pm.card.last4);
function subscriptionView(sub, expand = []) {
  const s = structuredClone(sub);
  if (expand.some((e) => e.startsWith('latest_invoice'))) s.latest_invoice = structuredClone(S.invoices.get(sub.latest_invoice));
  return s;
}

async function stripeApi(req, res, url) {
  const raw = (await readBody(req)).toString();
  const q = req.method === 'GET' ? parseForm(url.search.slice(1)) : parseForm(raw);
  const p = url.pathname;
  let m;

  // Demo-Kartenfeld (ersetzt Stripe.js, das ohne echtes Konto nicht lädt)
  if (p === '/demo/confirm_setup' && req.method === 'POST') {
    const b = JSON.parse(raw || '{}');
    const si = S.setup_intents.get(String(b.client_secret || '').split('_secret_')[0]);
    if (!si || si.client_secret !== b.client_secret) return json(res, 400, { error: { message: 'Die Sitzung ist abgelaufen. Bitte lade die Seite neu.' } });
    const num = String(b.number || '').replace(/\D/g, '');
    if (num.length < 15) return json(res, 400, { error: { message: 'Deine Kartennummer ist unvollständig.' } });
    if (!/^\d{2}\s*\/\s*\d{2}$/.test(String(b.exp || ''))) return json(res, 400, { error: { message: 'Das Ablaufdatum ist unvollständig.' } });
    if (String(b.cvc || '').replace(/\D/g, '').length < 3) return json(res, 400, { error: { message: 'Der Sicherheitscode ist unvollständig.' } });
    if (num.endsWith('0002') || num.endsWith('9995')) {
      log('💳 Karte abgelehnt (Testkarte)', '…' + num.slice(-4));
      return json(res, 402, { error: { message: 'Deine Karte wurde abgelehnt.' } });
    }
    const pm = { id: rid('pm'), object: 'payment_method', type: 'card', customer: si.customer,
      card: { brand: num.startsWith('4') ? 'visa' : num.startsWith('5') ? 'mastercard' : 'amex', last4: num.slice(-4), exp_month: +b.exp.split('/')[0], exp_year: 2000 + +b.exp.split('/')[1] } };
    S.payment_methods.set(pm.id, pm);
    Object.assign(si, { status: 'succeeded', payment_method: pm.id });
    log('💳 Karte gespeichert', pm.card.brand, '…' + pm.card.last4, '– 0 € abgebucht');
    webhook('setup_intent.succeeded', si);
    return json(res, 200, { setupIntent: { id: si.id, status: si.status } });
  }
  if (p === '/demo/portal') return portal(res, url);

  if (p === '/v1/customers' && req.method === 'POST') {
    const c = { id: rid('cus'), object: 'customer', email: q.email, name: q.name ?? null, metadata: q.metadata ?? {}, invoice_settings: { default_payment_method: null }, created: ts() };
    S.customers.set(c.id, c);
    return json(res, 200, c);
  }
  if ((m = /^\/v1\/customers\/(\w+)$/.exec(p))) {
    const c = S.customers.get(m[1]);
    if (!c) return stripeErr(res, 404, 'No such customer');
    if (req.method === 'POST') {
      if (q.invoice_settings?.default_payment_method) c.invoice_settings.default_payment_method = q.invoice_settings.default_payment_method;
      Object.assign(c.metadata, q.metadata ?? {});
    }
    return json(res, 200, c);
  }
  if (p === '/v1/setup_intents' && req.method === 'POST') {
    const id = rid('seti');
    const si = { id, object: 'setup_intent', client_secret: `${id}_secret_${crypto.randomBytes(12).toString('hex')}`, status: 'requires_payment_method',
      customer: q.customer, payment_method: null, usage: q.usage, metadata: q.metadata ?? {} };
    S.setup_intents.set(id, si);
    return json(res, 200, si);
  }
  if ((m = /^\/v1\/setup_intents\/(\w+)$/.exec(p))) {
    const si = S.setup_intents.get(m[1]);
    return si ? json(res, 200, si) : stripeErr(res, 404, 'No such setupintent');
  }
  if (p === '/v1/prices' && req.method === 'GET') {
    const keys = [].concat(q.lookup_keys ?? []);
    return json(res, 200, { object: 'list', data: PRICES.filter((x) => !keys.length || keys.includes(x.lookup_key)), has_more: false, url: '/v1/prices' });
  }
  if (p === '/v1/subscriptions' && req.method === 'POST') {
    const price = priceById(q.items?.[0]?.price);
    if (!price) return stripeErr(res, 400, 'No such price');
    const pm = S.payment_methods.get(q.default_payment_method);
    const ok = cardOk(pm);
    const id = rid('sub');
    const inv = { id: rid('in'), object: 'invoice', status: ok ? 'paid' : 'open', amount_due: price.unit_amount, amount_paid: ok ? price.unit_amount : 0, currency: 'eur',
      customer: q.customer, number: `VELOX-${String(S.invoices.size + 1).padStart(4, '0')}`, created: ts(),
      parent: { type: 'subscription_details', subscription_details: { subscription: id, metadata: q.metadata ?? {} } }, confirmation_secret: null,
      lines: { data: [{ description: `Velox Cut ${price.lookup_key.split('_')[1]} (${price.recurring.interval === 'year' ? 'jährlich' : 'monatlich'})`, amount: price.unit_amount }] } };
    S.invoices.set(inv.id, inv);
    const end = ts() + (price.recurring.interval === 'year' ? 365 : 30) * 86400;
    const sub = { id, object: 'subscription', status: ok ? 'active' : 'incomplete', customer: q.customer, default_payment_method: q.default_payment_method,
      metadata: q.metadata ?? {}, cancel_at_period_end: false, cancel_at: null, created: ts(), latest_invoice: inv.id,
      items: { object: 'list', data: [{ id: rid('si'), object: 'subscription_item', price, quantity: 1, current_period_start: ts(), current_period_end: end }] } };
    S.subscriptions.set(id, sub);
    if (ok) S.payments.push({ customer: q.customer, amount: price.unit_amount, text: inv.lines.data[0].description, number: inv.number, date: ts() });
    log(ok ? '🧾 Abo gestartet' : '🧾 Abo – Zahlung abgelehnt', price.lookup_key, (price.unit_amount / 100).toFixed(2), '€');
    (async () => {
      await webhook('customer.subscription.created', sub);
      if (ok) await webhook('invoice.paid', inv);
    })();
    return json(res, 200, subscriptionView(sub, [].concat(q.expand ?? [])));
  }
  if ((m = /^\/v1\/subscriptions\/(\w+)$/.exec(p))) {
    const sub = S.subscriptions.get(m[1]);
    if (!sub) return stripeErr(res, 404, 'No such subscription');
    if (req.method === 'DELETE') {
      Object.assign(sub, { status: 'canceled', canceled_at: ts() });
      webhook('customer.subscription.deleted', sub);
    } else if (req.method === 'POST') {
      if (q.cancel_at_period_end) sub.cancel_at_period_end = q.cancel_at_period_end === 'true';
      if (q.cancel_at) sub.cancel_at = +q.cancel_at;
      log('📝 Abo geändert', sub.id, sub.cancel_at_period_end ? '→ endet zum Laufzeitende' : '');
      webhook('customer.subscription.updated', sub);
    }
    return json(res, 200, subscriptionView(sub, [].concat(q.expand ?? [])));
  }
  if (p === '/v1/payment_intents' && req.method === 'POST') {
    const pm = S.payment_methods.get(q.payment_method);
    const ok = cardOk(pm);
    const id = rid('pi');
    const pi = { id, object: 'payment_intent', amount: +q.amount, currency: q.currency, customer: q.customer, payment_method: q.payment_method,
      status: ok ? 'succeeded' : 'requires_payment_method', client_secret: `${id}_secret_x`, description: q.description, metadata: q.metadata ?? {}, created: ts() };
    S.payment_intents.set(id, pi);
    if (!ok) return stripeErr(res, 402, 'Deine Karte wurde abgelehnt.', 'card_declined', 'card_error');
    S.payments.push({ customer: q.customer, amount: pi.amount, text: q.description, number: `VELOX-N${String(S.payments.length + 1).padStart(3, '0')}`, date: ts() });
    log('💶 Nachkauf bezahlt', (pi.amount / 100).toFixed(2), '€', q.description);
    webhook('payment_intent.succeeded', pi);
    return json(res, 200, pi);
  }
  if ((m = /^\/v1\/payment_intents\/(\w+)$/.exec(p))) {
    const pi = S.payment_intents.get(m[1]);
    return pi ? json(res, 200, pi) : stripeErr(res, 404, 'No such payment_intent');
  }
  if (p === '/v1/billing_portal/sessions' && req.method === 'POST') {
    const u = `http://localhost:${ST_PORT}/demo/portal?c=${q.customer}&zurueck=${encodeURIComponent(q.return_url)}`;
    return json(res, 200, { id: rid('bps'), object: 'billing_portal.session', url: u });
  }
  return stripeErr(res, 404, `Unrecognized request URL (${req.method}: ${p}) – im Demo nicht nachgebildet`);
}

/** Einfaches Kundenportal (in echt: von Stripe gehostet) */
function portal(res, url) {
  const c = S.customers.get(url.searchParams.get('c'));
  const back = url.searchParams.get('zurueck') || APP;
  if (!c) return send(res, 404, 'Kunde unbekannt');
  const pm = S.payment_methods.get(c.invoice_settings.default_payment_method);
  const sub = [...S.subscriptions.values()].reverse().find((s) => s.customer === c.id && s.status !== 'canceled');
  const eur = (n) => (n / 100).toLocaleString('de-DE', { style: 'currency', currency: 'EUR' });
  const d = (t) => new Date(t * 1000).toLocaleDateString('de-DE');
  const rows = S.payments.filter((x) => x.customer === c.id).reverse()
    .map((x) => `<tr><td>${d(x.date)}</td><td>${x.text}</td><td>${x.number}</td><td style="text-align:right">${eur(x.amount)}</td><td>Bezahlt</td></tr>`).join('');
  const html = `<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kundenportal (Demo)</title>
<style>body{font:15px/1.5 system-ui,-apple-system,sans-serif;background:#f6f9fc;color:#1a1f36;margin:0}.bar{background:#fde68a;padding:8px 16px;text-align:center;font-weight:600}
main{max-width:760px;margin:0 auto;padding:28px 16px}a.back{color:#635bff;text-decoration:none;font-weight:600}h1{font-size:22px;margin:18px 0 4px}h2{font-size:14px;text-transform:uppercase;color:#697386;margin:28px 0 8px;letter-spacing:.04em}
.card{background:#fff;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.08);padding:16px 18px}table{width:100%;border-collapse:collapse}td{padding:8px 4px;border-bottom:1px solid #eef0f4;font-size:14px}
.muted{color:#697386}</style>
<div class="bar">DEMO – nachgebildetes Stripe-Kundenportal (in echt hostet Stripe diese Seite)</div>
<main><a class="back" href="${back}">← Zurück zu Velox Cut</a><h1>Velox Cut</h1><p class="muted">${c.email}</p>
<h2>Aktuelles Abo</h2><div class="card">${sub ? `<b>${sub.items.data[0].price.lookup_key.replace('velox_', '').replace('_', ' · ')}</b> – ${eur(sub.items.data[0].price.unit_amount)}<br><span class="muted">${sub.cancel_at_period_end ? 'Endet am ' : 'Verlängert sich am '}${d(sub.items.data[0].current_period_end)}</span>` : '<span class="muted">Kein Abo – Free</span>'}</div>
<h2>Zahlungsmethode</h2><div class="card">${pm ? `${pm.card.brand.toUpperCase()} •••• ${pm.card.last4} <span class="muted">· gültig bis ${String(pm.card.exp_month).padStart(2, '0')}/${pm.card.exp_year}</span>` : '—'}</div>
<h2>Rechnungsverlauf</h2><div class="card">${rows ? `<table>${rows}</table>` : '<span class="muted">Noch keine Zahlungen</span>'}</div>
<p class="muted" style="margin-top:22px;font-size:13px">Gemäß § 19 UStG wird keine Umsatzsteuer berechnet.</p></main>`;
  return send(res, 200, html, { 'Content-Type': 'text/html; charset=utf-8' });
}

// ───────────────────────────── HTTP ─────────────────────────────
function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}
const CORS = {
  'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*', 'Access-Control-Expose-Headers': '*',
};
function send(res, status, body = '', headers = {}) {
  res.writeHead(status, { ...CORS, ...headers });
  res.end(body);
}
const json = (res, status, obj) => send(res, status, obj === null ? 'null' : JSON.stringify(obj), { 'Content-Type': 'application/json' });
async function sendFile(req, res, file, headers = {}) {
  let data;
  try { data = await readFile(file); } catch { return json(res, 404, { message: 'Datei nicht gefunden' }); }
  const range = /bytes=(\d*)-(\d*)/.exec(req.headers.range || '');
  const base = { 'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes', ...headers };
  if (!range) return send(res, 200, data, { ...base, 'Content-Length': data.length });
  const start = range[1] ? +range[1] : 0;
  const end = range[2] ? +range[2] : data.length - 1;
  return send(res, 206, data.subarray(start, end + 1), { ...base, 'Content-Range': `bytes ${start}-${end}/${data.length}`, 'Content-Length': end - start + 1 });
}

function roleOf(req) {
  const bearer = (req.headers.authorization || '').replace(/^Bearer /i, '');
  if (bearer === SERVICE_KEY) return ['service', null];
  const u = userFromToken(bearer);
  return u ? ['user', u.id] : ['anon', null];
}

http.createServer(async (req, res) => {
  if (req.method === 'OPTIONS') return send(res, 204);
  const url = new URL(req.url, `http://localhost:${SB_PORT}`);
  try {
    const [role, uid] = roleOf(req);
    if (url.pathname.startsWith('/auth/v1')) return await auth(req, res, url);
    if (url.pathname.startsWith('/rest/v1')) return await rest(req, res, url, role, uid);
    if (url.pathname.startsWith('/storage/v1')) return await storage(req, res, url, role);
    if (url.pathname === '/demo/state') return json(res, 200, { users: [...users.keys()], ...db });
    json(res, 404, { message: 'Nicht nachgebildet' });
  } catch (e) {
    log('Fehler', req.method, url.pathname, e);
    json(res, 500, { message: e.message });
  }
}).listen(SB_PORT, () => log(`Supabase-Nachbildung läuft auf http://localhost:${SB_PORT}`));

http.createServer(async (req, res) => {
  if (req.method === 'OPTIONS') return send(res, 204);
  const url = new URL(req.url, `http://localhost:${ST_PORT}`);
  try {
    await stripeApi(req, res, url);
  } catch (e) {
    log('Stripe-Fehler', req.method, url.pathname, e);
    stripeErr(res, 500, e.message, 'api_error', 'api_error');
  }
}).listen(ST_PORT, () => log(`Stripe-Nachbildung (Testmodus) läuft auf http://localhost:${ST_PORT}`));
