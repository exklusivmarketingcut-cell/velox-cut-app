import { revalidatePath } from 'next/cache';
import { createAdmin, getUser } from '@/lib/supabase/server';
import { isAdmin } from '@/lib/admin';
import { describeCode, normCode, type Code } from '@/lib/codes';
import { PLANS } from '@/lib/pricing';

async function requireAdmin() {
  const user = await getUser();
  if (!isAdmin(user?.email)) throw new Error('nicht erlaubt');
}

// Server-Aktionen (laufen nur auf dem Server, nur für Admins)
async function createCode(form: FormData) {
  'use server';
  await requireAdmin();
  const art = form.get('art') === 'gratis_monat' ? 'gratis_monat' : 'credits';
  const num = (k: string) => { const n = parseInt(String(form.get(k) ?? ''), 10); return Number.isFinite(n) && n > 0 ? n : null; };
  const until = String(form.get('valid_until') ?? '');
  await createAdmin().from('codes').insert({
    code: normCode(form.get('code')),
    art,
    credits: art === 'credits' ? num('credits') ?? 0 : 0,
    plan: art === 'gratis_monat' ? String(form.get('plan') || 'starter') : null,
    max_uses: num('max_uses'),
    valid_until: /^\d{4}-\d{2}-\d{2}$/.test(until) ? `${until}T23:59:59+02:00` : null,
    note: String(form.get('note') ?? '').slice(0, 200) || null,
  });
  revalidatePath('/admin');
}

async function toggleCode(form: FormData) {
  'use server';
  await requireAdmin();
  const code = normCode(form.get('code'));
  await createAdmin().from('codes').update({ active: form.get('active') === '1' }).eq('code', code);
  revalidatePath('/admin');
}

export default async function Codes() {
  const { data, error } = await createAdmin().from('codes').select('*').order('created_at', { ascending: false });
  if (error) {
    return (
      <section className="card stack">
        <h2 style={{ margin: 0 }}>Gutschein-Codes</h2>
        <p className="muted" style={{ margin: 0 }}>Noch nicht eingerichtet: Bitte einmal <code>supabase/einmal-ausfuehren-0008.sql</code> im Supabase-SQL-Editor ausführen.</p>
      </section>
    );
  }
  const codes = (data ?? []) as Code[];
  return (
    <section className="card stack">
      <h2 style={{ margin: 0 }}>Gutschein-Codes</h2>
      <p className="muted" style={{ margin: 0 }}>
        Link zum Weitergeben: <code>velox-cut.de/registrieren?code=DEINCODE</code> – oder der Kunde gibt den Code im Konto unter „Gutschein-Code“ ein.
        Jedes Konto und jede Karte kann einen Code nur einmal einlösen.
      </p>
      <form action={createCode} className="adm-form">
        <label>Code<input className="inp" name="code" required pattern="[A-Za-z0-9_\-]{3,30}" placeholder="GASTRO" style={{ textTransform: 'uppercase' }} /></label>
        <label>Art
          <select className="inp" name="art" defaultValue="gratis_monat">
            <option value="gratis_monat">Gratis-Monat (Abo, danach regulär)</option>
            <option value="credits">Credits geschenkt (ohne Abo)</option>
          </select>
        </label>
        <label>Paket (bei Gratis-Monat)
          <select className="inp" name="plan" defaultValue="starter">
            {(['starter', 'pro', 'business'] as const).map((p) => <option key={p} value={p}>{PLANS[p].name} ({PLANS[p].credits} Credits)</option>)}
          </select>
        </label>
        <label>Credits (bei Geschenk)<input className="inp" name="credits" type="number" min="1" max="5000" placeholder="50" /></label>
        <label>Max. Einlösungen<input className="inp" name="max_uses" type="number" min="1" placeholder="leer = unbegrenzt" /></label>
        <label>Gültig bis<input className="inp" name="valid_until" type="date" /></label>
        <label style={{ gridColumn: '1 / -1' }}>Notiz (nur für dich)<input className="inp" name="note" placeholder="z. B. Gastro-Akquise Oktober" /></label>
        <button className="btn btn-acc" type="submit" style={{ justifySelf: 'start' }}>Code anlegen</button>
      </form>
      {codes.length ? (
        <div className="adm-table">
          <table>
            <thead><tr><th>Code</th><th>Was</th><th>Eingelöst</th><th>Gültig bis</th><th>Notiz</th><th></th></tr></thead>
            <tbody>
              {codes.map((c) => (
                <tr key={c.code} style={{ opacity: c.active ? 1 : 0.5 }}>
                  <td><b>{c.code}</b></td>
                  <td style={{ whiteSpace: 'normal', minWidth: 220 }}>{describeCode(c)}</td>
                  <td>{c.uses}{c.max_uses ? ` / ${c.max_uses}` : ''}</td>
                  <td>{c.valid_until ? new Date(c.valid_until).toLocaleDateString('de-DE') : '–'}</td>
                  <td>{c.note ?? ''}</td>
                  <td>
                    <form action={toggleCode}>
                      <input type="hidden" name="code" value={c.code} />
                      <input type="hidden" name="active" value={c.active ? '0' : '1'} />
                      <button className="btn" style={{ padding: '5px 12px', fontSize: 13 }}>{c.active ? 'Pausieren' : 'Aktivieren'}</button>
                    </form>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
