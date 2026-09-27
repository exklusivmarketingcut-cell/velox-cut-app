import AuthShell from '@/components/AuthShell';
import LoginForm from './LoginForm';
import { safePath } from '@/lib/choice';

export const metadata = { title: 'Einloggen' };

export default async function Page({ searchParams }: PageProps<'/login'>) {
  const q = await searchParams;
  const weiter = safePath(q.weiter);
  return (
    <AuthShell title="Willkommen zurück" sub="Log dich ein, um weiterzuschneiden.">
      <LoginForm weiter={weiter} />
    </AuthShell>
  );
}
