import AuthShell from '@/components/AuthShell';
import { requireProfile } from '@/lib/profile';
import { afterCard, parseChoice } from '@/lib/choice';
import { redirect } from 'next/navigation';
import CardForm from './CardForm';
import DemoCard from './DemoCard';

export const metadata = { title: 'Karte hinterlegen' };

export default async function Page({ searchParams }: PageProps<'/karte'>) {
  const c = parseChoice(await searchParams);
  const next = afterCard(c);
  const p = await requireProfile();
  if (p.card_on_file) redirect(next);
  return (
    <AuthShell
      title="Karte hinterlegen"
      sub={`${c.paket === 'free' ? '' : 'Schritt 2 von 3 · '}Es wird nichts abgebucht. Du zahlst nur, wenn du selbst ein Paket startest oder Credits nachkaufst.`}
    >
      {process.env.NEXT_PUBLIC_DEMO === '1' ? <DemoCard next={next} /> : <CardForm publishableKey={process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY!} next={next} />}
    </AuthShell>
  );
}
