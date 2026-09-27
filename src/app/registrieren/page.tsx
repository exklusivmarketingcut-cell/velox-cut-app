import Link from 'next/link';
import { redirect } from 'next/navigation';
import AuthShell from '@/components/AuthShell';
import { getUser } from '@/lib/supabase/server';
import { afterCard, choiceQuery, parseChoice } from '@/lib/choice';
import { PLANS, euro } from '@/lib/pricing';
import SignupForm from './SignupForm';

export const metadata = { title: 'Konto erstellen' };

export default async function Page({ searchParams }: PageProps<'/registrieren'>) {
  const c = parseChoice(await searchParams);
  // schon eingeloggt → direkt weiter zum nächsten Schritt
  if (await getUser()) redirect(c.paket === 'free' ? '/studio' : afterCard(c));
  const plan = PLANS[c.paket];
  const price = c.paket === 'free' ? null : c.intervall === 'yearly' ? `${euro(plan.yearly)} / Jahr` : `${euro(plan.monthly)} / Monat`;
  return (
    <AuthShell
      title={c.paket === 'free' ? 'Gratis-Konto erstellen' : `Mit ${plan.name} starten`}
      sub={c.paket === 'free' ? '1 Video gratis – 0 €, bis du selbst etwas kaufst.' : 'Schritt 1 von 3: Konto · Karte · Paket bestätigen'}
    >
      <div className="choice">
        <div>
          <b>{plan.name}</b>
          <span>{price ?? '0 €'} · {plan.credits} Credits{c.paket === 'free' ? ' (≈ 1 Video)' : ' pro Monat'}</span>
        </div>
        <Link href="/#pricing">ändern</Link>
      </div>
      <SignupForm next={`/karte${choiceQuery(c)}`} loginHref={`/login?weiter=${encodeURIComponent(c.paket === 'free' ? '/studio' : afterCard(c))}`} />
    </AuthShell>
  );
}
