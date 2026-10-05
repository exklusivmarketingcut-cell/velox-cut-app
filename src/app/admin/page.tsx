import { notFound } from 'next/navigation';
import Topbar from '@/components/Topbar';
import { requireProfile } from '@/lib/profile';
import { createAdmin } from '@/lib/supabase/server';
import { PLANS, euro } from '@/lib/pricing';
import { isAdmin } from '@/lib/admin';

export const metadata = { title: 'Admin', robots: { index: false } };
export const dynamic = 'force-dynamic';


type Job = {
  id: string; user_id: string; status: string; style: string; clips: number; source_kind: string; source_name: string | null;
  credits_charged: number; error: string | null; created_at: string; started_at: string | null; finished_at: string | null;
  outputs: unknown[]; stage: string | null;
};

const ago = (iso: string | null) => {
  if (!iso) return '–';
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60e3);
  return m < 1 ? 'gerade' : m < 60 ? `vor ${m} Min.` : m < 1440 ? `vor ${Math.round(m / 60)} Std.` : `vor ${Math.round(m / 1440)} Tg.`;
};
const dt = (iso: string) => new Date(iso).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short', timeZone: 'Europe/Berlin' });

export default async function Page() {
  const p = await requireProfile();
  if (!isAdmin(p.email)) notFound();
  const db = createAdmin();
  const d7 = new Date(Date.now() - 7 * 864e5).toISOString();
  const d30 = new Date(Date.now() - 30 * 864e5).toISOString();
  const [{ data: profiles }, { data: jobs }, { data: workers }, { data: topups }] = await Promise.all([
    db.from('profiles').select('id, email, plan, plan_interval, plan_status, card_on_file, plan_credits, topup_credits, created_at').order('created_at', { ascending: false }).limit(1000),
    db.from('jobs').select('id, user_id, status, style, clips, source_kind, source_name, credits_charged, error, created_at, started_at, finished_at, outputs, stage').gte('created_at', d30).order('created_at', { ascending: false }).limit(1000),
    db.from('workers').select('*'),
    db.from('credit_ledger').select('user_id, delta, ref, created_at').eq('reason', 'topup').gte('created_at', d30),
  ]);
  const users = profiles ?? [];
  const js = (jobs ?? []) as Job[];
  const email = new Map(users.map((u) => [u.id, u.email as string]));

  const paying = users.filter((u) => u.plan !== 'free' && u.plan_status !== 'canceled');
  const mrr = paying.reduce((s, u) => {
    const pl = PLANS[u.plan as keyof typeof PLANS];
    return s + (u.plan_interval === 'yearly' ? pl.yearly / 12 : pl.monthly);
  }, 0);
  const packs = (topups ?? []).filter((t) => !String(t.ref ?? '').startsWith('welcome-'));
  const jobs7 = js.filter((j) => j.created_at >= d7);
  const done7 = jobs7.filter((j) => j.status === 'done');
  const failed7 = jobs7.filter((j) => j.status === 'failed');
  const durs = done7.filter((j) => j.started_at && j.finished_at).map((j) => (new Date(j.finished_at!).getTime() - new Date(j.started_at!).getTime()) / 60e3).sort((a, b) => a - b);
  const medDur = durs.length ? durs[Math.floor(durs.length / 2)] : null;
  const waiting = js.filter((j) => j.status === 'queued');
  const active = new Set(jobs7.map((j) => j.user_id)).size;
  const w = (workers ?? [])[0] as { name: string; last_seen: string; busy_job: string | null } | undefined;
  const online = w && Date.now() - new Date(w.last_seen).getTime() < 120e3;
  const errors = new Map<string, number>();
  failed7.forEach((j) => errors.set(j.error || 'ohne Meldung', (errors.get(j.error || 'ohne Meldung') ?? 0) + 1));

  const kpi: [string, string, string?][] = [
    ['Kunden', String(users.length), `${users.filter((u) => u.created_at >= d7).length} neu in 7 Tagen`],
    ['Zahlende Abos', String(paying.length), `≈ ${euro(Math.round(mrr))} / Monat`],
    ['Nachkäufe (30 T.)', String(packs.length), `${packs.reduce((s, t) => s + t.delta, 0)} Credits`],
    ['Aktive Kunden (7 T.)', String(active), `${users.filter((u) => u.card_on_file).length} mit Karte`],
    ['Videos (7 T.)', String(done7.length), `${failed7.length} fehlgeschlagen${jobs7.length ? ` · ${Math.round((100 * failed7.length) / jobs7.length)} %` : ''}`],
    ['Schnittdauer', medDur ? `${medDur.toFixed(1)} Min.` : '–', 'Median, 7 Tage'],
  ];

  return (
    <>
      <Topbar p={p} on="admin" />
      <main className="page">
        <div className="wrap stack" style={{ gap: 20 }}>
          <div>
            <h1>Admin</h1>
            <p className="sub">
              Schnitt-Maschine: <b style={{ color: online ? 'var(--ok)' : 'var(--err)' }}>{online ? 'läuft' : 'nicht erreichbar'}</b>
              {w ? <> · zuletzt {ago(w.last_seen)}{w.busy_job ? ' · schneidet gerade' : ''}</> : null}
              {waiting.length ? <> · <b>{waiting.length} wartend</b>{waiting[0]?.stage ? ` („${waiting[0].stage}“)` : ''}</> : null}
            </p>
          </div>
          <div className="adm-kpis">
            {kpi.map(([k, v, s]) => (
              <div className="card adm-kpi" key={k}><small>{k}</small><b>{v}</b>{s ? <span className="muted">{s}</span> : null}</div>
            ))}
          </div>
          {errors.size ? (
            <section className="card stack">
              <h2 style={{ margin: 0 }}>Fehler der letzten 7 Tage</h2>
              <ul className="adm-list">{[...errors].sort((a, b) => b[1] - a[1]).map(([e, n]) => <li key={e}><b>{n}×</b> {e}</li>)}</ul>
            </section>
          ) : null}
          <section className="card stack">
            <h2 style={{ margin: 0 }}>Letzte Aufträge</h2>
            <div className="adm-table">
              <table>
                <thead><tr><th>Zeit</th><th>Kunde</th><th>Quelle</th><th>Stil</th><th>Status</th><th>Videos</th><th>Credits</th></tr></thead>
                <tbody>
                  {js.slice(0, 40).map((j) => (
                    <tr key={j.id}>
                      <td>{dt(j.created_at)}</td>
                      <td>{email.get(j.user_id) ?? '?'}</td>
                      <td>{j.source_name || j.source_kind}</td>
                      <td>{j.style}</td>
                      <td><span className={`badge ${j.status}`}>{j.status}</span>{j.error ? <small className="muted"> {j.error.slice(0, 60)}</small> : null}</td>
                      <td>{j.outputs?.length ?? 0}/{j.clips}</td>
                      <td>{j.credits_charged}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className="card stack">
            <h2 style={{ margin: 0 }}>Neueste Kunden</h2>
            <div className="adm-table">
              <table>
                <thead><tr><th>Seit</th><th>E-Mail</th><th>Paket</th><th>Karte</th><th>Credits</th></tr></thead>
                <tbody>
                  {users.slice(0, 30).map((u) => (
                    <tr key={u.id}>
                      <td>{dt(u.created_at)}</td>
                      <td>{u.email}</td>
                      <td>{PLANS[u.plan as keyof typeof PLANS]?.name}{u.plan_interval === 'yearly' ? ' (jährl.)' : ''}{u.plan_status && u.plan !== 'free' ? ` · ${u.plan_status}` : ''}</td>
                      <td>{u.card_on_file ? '✓' : '–'}</td>
                      <td>{u.plan_credits + u.topup_credits}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </main>
    </>
  );
}
