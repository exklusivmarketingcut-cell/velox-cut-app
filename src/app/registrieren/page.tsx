import { redirect } from 'next/navigation';
import AuthShell from '@/components/AuthShell';
import { getUser } from '@/lib/supabase/server';
import { afterCard, choiceQuery, parseChoice } from '@/lib/choice';
import { PLANS, euro, type PlanId } from '@/lib/pricing';
import PlanPicker from './PlanPicker';
import SignupForm from './SignupForm';

export const metadata = { title: 'Konto erstellen' };

export default async function Page({ searchParams }: PageProps<'/registrieren'>) {
  const c = parseChoice(await searchParams);
  // schon eingeloggt → direkt weiter zum nächsten Schritt
  if (await getUser()) redirect(c.paket === 'free' ? '/studio' : afterCard(c));
  const plan = PLANS[c.paket];
  return (
    <AuthShell
      title={c.paket === 'free' ? 'Gratis-Konto erstellen' : `Mit ${plan.name} starten`}
      sub={c.paket === 'free' ? '20 Credits zum Start – genug für 1 Viral- oder 2 Clean-Videos. 0 €, bis du selbst etwas kaufst.' : 'Schritt 1 von 3: Konto · Karte · Paket bestätigen'}
    >
      <PlanPicker
        paket={c.paket}
        intervall={c.intervall}
        plans={(Object.keys(PLANS) as PlanId[]).map((id) => ({ id, name: PLANS[id].name, credits: PLANS[id].credits, monthlyTxt: euro(PLANS[id].monthly), yearlyTxt: euro(PLANS[id].yearly), yearlyPerMonthTxt: euro(Math.round(PLANS[id].yearly / 12)) }))}
      />
      <SignupForm next={`/karte${choiceQuery(c)}`} loginHref={`/login?weiter=${encodeURIComponent(c.paket === 'free' ? '/studio' : afterCard(c))}`} />
    </AuthShell>
  );
}
