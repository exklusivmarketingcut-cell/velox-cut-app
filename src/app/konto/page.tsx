import Topbar from '@/components/Topbar';
import { requireProfile } from '@/lib/profile';
import { createClient } from '@/lib/supabase/server';
import { PACKS, PLANS, euro } from '@/lib/pricing';
import Billing from './Billing';
import { parseChoice } from '@/lib/choice';

export const metadata = { title: 'Abo & Credits' };

const REASON: Record<string, string> = {
  signup: 'Willkommens-Credits', free_refill: 'Monats-Credits (Free)', plan_period: 'Paket-Credits', yearly_month: 'Monats-Credits (Jahres-Abo)',
  topup: 'Nachkauf', job: 'Video geschnitten', refund: 'Erstattung', downgrade: 'Wechsel zu Free',
};

export default async function Page({ searchParams }: PageProps<'/konto'>) {
  const q = await searchParams;
  const choice = parseChoice(q);
  const p = await requireProfile({ needCard: true });
  const supabase = await createClient();
  const { data: ledger } = await supabase.from('credit_ledger').select('*').order('created_at', { ascending: false }).limit(15);
  const plans = (['starter', 'pro', 'business'] as const).map((id) => ({ id, ...PLANS[id] }));
  const packs = (Object.keys(PACKS) as (keyof typeof PACKS)[]).map((id) => ({ id, ...PACKS[id] }));
  return (
    <>
      <Topbar p={p} on="konto" />
      <main className="page">
        <div className="wrap stack" style={{ gap: 20 }}>
          <div>
            <h1>Abo &amp; Credits</h1>
            <p className="sub">
              Paket <b>{PLANS[p.plan].name}</b>{p.plan_interval ? ` (${p.plan_interval === 'yearly' ? 'jährlich' : 'monatlich'})` : ''}
              {p.plan_status && p.plan !== 'free' ? ` · Status: ${p.plan_status === 'active' ? 'aktiv' : p.plan_status}` : ''}
              {' · '}<b>{p.plan_credits}</b> Paket-Credits + <b>{p.topup_credits}</b> nachgekaufte Credits (verfallen nie)
            </p>
          </div>
          <Billing
            current={p.plan}
            hasSub={!!p.subscription_id && p.plan_status !== 'canceled' && p.plan_status !== 'incomplete'}
            plans={plans.map((x) => ({ ...x, monthlyTxt: euro(x.monthly), yearlyTxt: euro(x.yearly) }))}
            packs={packs.map((x) => ({ ...x, priceTxt: euro(x.price) }))}
            publishableKey={process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY!}
            choice={choice.paket === 'free' ? null : { plan: choice.paket, interval: choice.intervall }}
          />
          <section className="card">
            <h2 style={{ font: '700 20px var(--display)', marginBottom: 10 }}>Verlauf</h2>
            {(ledger ?? []).map((l) => (
              <div key={l.id} className="row" style={{ justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid var(--line)' }}>
                <span>{REASON[l.reason] ?? l.reason} <span className="muted">· {new Date(l.created_at).toLocaleDateString('de-DE')}</span></span>
                <b style={{ color: l.delta >= 0 ? 'var(--ok)' : 'var(--txt)' }}>{l.delta > 0 ? '+' : ''}{l.delta}</b>
              </div>
            ))}
          </section>
          <p className="note">
            Alle Preise sind Endpreise – gemäß § 19 UStG wird keine Umsatzsteuer berechnet. Es gelten unsere <a href="/agb.html">AGB</a> und die <a href="/widerruf.html">Widerrufsbelehrung</a>.
            {' '}<a href="/kuendigen.html">Verträge hier kündigen</a>
          </p>
        </div>
      </main>
    </>
  );
}
