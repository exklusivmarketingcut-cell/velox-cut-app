import AuthShell from '@/components/AuthShell';
import LoginForm from './LoginForm';

export const metadata = { title: 'Einloggen' };

export default async function Page({ searchParams }: PageProps<'/login'>) {
  const q = await searchParams;
  const weiter = typeof q.weiter === 'string' && q.weiter.startsWith('/') ? q.weiter : '/studio';
  return (
    <AuthShell title="Willkommen zurück" sub="Log dich ein, um weiterzuschneiden.">
      <LoginForm weiter={weiter} />
    </AuthShell>
  );
}
