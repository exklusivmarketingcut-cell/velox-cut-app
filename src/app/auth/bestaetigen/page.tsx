import { redirect } from 'next/navigation';
import AuthShell from '@/components/AuthShell';
import { safePath } from '@/lib/choice';

export const metadata = { title: 'Bestätigen' };

const TYPES = ['signup', 'recovery', 'email_change', 'email', 'magiclink', 'invite'] as const;

/**
 * Ziel des Links aus unseren E-Mails (Registrierung, Passwort vergessen) – liegt auf velox-cut.de statt auf supabase.co.
 * Bestätigt wird erst per Knopfdruck (POST an /auth/callback): Mail-Scanner (z. B. Outlook „Safe Links“) rufen Links
 * vorab auf und würden den Einmal-Code sonst verbrauchen, bevor der Kunde klickt.
 */
export default async function Page({ searchParams }: PageProps<'/auth/bestaetigen'>) {
  const q = await searchParams;
  const tokenHash = typeof q.token_hash === 'string' ? q.token_hash : '';
  const type = TYPES.find((t) => t === q.type) ?? 'signup';
  const weiter = safePath(q.weiter);
  const recovery = type === 'recovery';
  // Standard-Vorlage von Supabase (noch nicht umgestellt): Link kommt mit ?code= → wie bisher tauschen
  if (!tokenHash && typeof q.code === 'string') redirect(`/auth/callback?code=${encodeURIComponent(q.code)}&weiter=${encodeURIComponent(weiter)}`);

  if (!tokenHash)
    return (
      <AuthShell title="Link unvollständig" sub="Bitte öffne den Link direkt aus der E-Mail oder fordere eine neue an.">
        <a className="btn btn-acc full" href="/login">Zum Login</a>
      </AuthShell>
    );

  return (
    <AuthShell
      title={recovery ? 'Neues Passwort festlegen' : 'Fast geschafft'}
      sub={recovery ? 'Ein Klick, dann kannst du dein neues Passwort eingeben.' : 'Ein Klick, dann ist dein Velox-Cut-Konto bestätigt.'}
    >
      <form className="stack" method="post" action="/auth/callback">
        <input type="hidden" name="token_hash" value={tokenHash} />
        <input type="hidden" name="type" value={type} />
        <input type="hidden" name="weiter" value={weiter} />
        <button className="btn btn-acc full">{recovery ? 'Weiter zum neuen Passwort →' : 'E-Mail bestätigen →'}</button>
      </form>
    </AuthShell>
  );
}
