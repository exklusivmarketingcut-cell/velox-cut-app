import { redirect } from 'next/navigation';
import AuthShell from '@/components/AuthShell';
import { getUser } from '@/lib/supabase/server';
import { afterCard, choiceQuery, parseChoice } from '@/lib/choice';
import { PLANS, euro, type PlanId } from '@/lib/pricing';
import PlanPicker from './PlanPicker';
import SignupForm from './SignupForm';

export const metadata = { title: 'Konto erstellen' };

export default async function Page({ searchParams }: PageProps<'/registrieren'>) {
  const q = await searchParams;
  const c = parseChoice(q);
  const code = typeof q.code === 'string' && /^[A-Za-z0-9_-]{3,30}$/.test(q.code) ? q.code.toUpperCase() : undefined;
  const einladung = typeof q.einladung === 'string' && /^[0-9a-f-]{36}$/i.test(q.einladung) ? q.einladung : undefined;
  // schon eingeloggt → direkt weiter zum nächsten Schritt
  if (await getUser()) redirect(c.paket === 'free' ? '/studio' : afterCard(c));
  const plan = PLANS[c.paket];
  return (
    <AuthShell
      title={c.paket === 'free' ? 'Gratis-Konto erstellen' : `Mit ${plan.name} starten`}
      sub={c.paket === 'free' ? '10 Credits zum Start – dein erstes Video ist gratis. 0 €, bis du selbst etwas kaufst.' : 'Schritt 1 von 3: Konto · Karte · Paket bestätigen'}
    >
      <PlanPicker
        paket={c.paket}
        intervall={c.intervall}
        plans={(Object.keys(PLANS) as PlanId[]).map((id) => ({ id, name: PLANS[id].name, credits: PLANS[id].credits, monthlyTxt: euro(PLANS[id].monthly), yearlyTxt: euro(PLANS[id].yearly), yearlyPerMonthTxt: euro(Math.round(PLANS[id].yearly / 12)) }))}
      />
      {einladung ? <p className="hint" style={{ margin: 0 }}>🎁 Du wurdest eingeladen: Beim ersten Kauf bekommst du 50 Bonus-Credits obendrauf.</p> : null}
      {code ? <p className="hint" style={{ margin: 0 }}>🎁 Dein Code <b>{code}</b> wird nach der Anmeldung im Konto eingelöst.</p> : null}
      <SignupForm invite={einladung} code={code} next={`/karte${choiceQuery(c)}`} loginHref={`/login?weiter=${encodeURIComponent(c.paket === 'free' ? '/studio' : afterCard(c))}`} />
    </AuthShell>
  );
}
