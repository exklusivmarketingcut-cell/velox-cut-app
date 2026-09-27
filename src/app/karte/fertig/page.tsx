import AuthShell from '@/components/AuthShell';
import Confirm from './Confirm';

// Rückkehr nach einer Bank-Bestätigung (z. B. 3-D Secure mit Weiterleitung)
export default async function Page({ searchParams }: PageProps<'/karte/fertig'>) {
  const q = await searchParams;
  const id = typeof q.setup_intent === 'string' ? q.setup_intent : '';
  return (
    <AuthShell title="Karte wird bestätigt …">
      {id ? <Confirm id={id} /> : <p className="err">Es fehlt eine Angabe. Bitte hinterlege die Karte noch einmal.</p>}
    </AuthShell>
  );
}
