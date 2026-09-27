import AuthShell from '@/components/AuthShell';
import { requireProfile } from '@/lib/profile';
import { redirect } from 'next/navigation';
import CardForm from './CardForm';
import DemoCard from './DemoCard';

export const metadata = { title: 'Karte hinterlegen' };

export default async function Page() {
  const p = await requireProfile();
  if (p.card_on_file) redirect('/studio');
  return (
    <AuthShell title="Karte hinterlegen" sub="Es wird nichts abgebucht. Du zahlst nur, wenn du selbst ein Paket startest oder Credits nachkaufst.">
      {process.env.NEXT_PUBLIC_DEMO === '1' ? <DemoCard /> : <CardForm publishableKey={process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY!} />}
    </AuthShell>
  );
}
