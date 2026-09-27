import AuthShell from '@/components/AuthShell';
import SignupForm from './SignupForm';

export const metadata = { title: 'Konto erstellen' };

export default function Page() {
  return (
    <AuthShell title="Gratis-Konto erstellen" sub="1 Video gratis – 0 €, bis du selbst etwas kaufst.">
      <SignupForm />
    </AuthShell>
  );
}
