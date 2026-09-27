import AuthShell from '@/components/AuthShell';
import Confirm from './Confirm';
import { safePath } from '@/lib/choice';

// Rückkehr nach einer Bank-Bestätigung (z. B. 3-D Secure mit Weiterleitung)
export default async function Page({ searchParams }: PageProps<'/karte/fertig'>) {
  const q = await searchParams;
  const id = typeof q.setup_intent === 'string' ? q.setup_intent : '';
  const weiter = safePath(q.weiter, '/studio?willkommen=1');
  return (
    <AuthShell title="Karte wird bestätigt …">
      {id ? <Confirm id={id} next={weiter} /> : <p className="err">Es fehlt eine Angabe. Bitte hinterlege die Karte noch einmal.</p>}
    </AuthShell>
  );
}
