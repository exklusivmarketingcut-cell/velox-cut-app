'use client';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { createClient } from '@/lib/supabase/client';

export default function LoginForm({ weiter }: { weiter: string }) {
  const router = useRouter();
  const [err, setErr] = useState('');
  const [info, setInfo] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setErr('');
    const { error } = await createClient().auth.signInWithPassword({
      email: String(f.get('email') || '').trim(),
      password: String(f.get('password') || ''),
    });
    setBusy(false);
    if (error) return setErr('E-Mail oder Passwort stimmt nicht.');
    router.replace(weiter);
    router.refresh();
  }

  async function forgot(e: React.MouseEvent) {
    e.preventDefault();
    const email = (document.querySelector('input[name=email]') as HTMLInputElement).value.trim();
    if (!email) return setErr('Gib zuerst deine E-Mail ein.');
    await createClient().auth.resetPasswordForEmail(email, { redirectTo: `${location.origin}/auth/bestaetigen?weiter=/passwort-neu` });
    setErr('');
    setInfo('Falls es ein Konto gibt, haben wir dir einen Link zum Zurücksetzen geschickt.');
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <label className="f">E-Mail<input className="inp" name="email" type="email" autoComplete="email" required /></label>
      <label className="f">Passwort<input className="inp" name="password" type="password" autoComplete="current-password" required /></label>
      {err ? <p className="err">{err}</p> : null}
      {info ? <p className="ok">{info}</p> : null}
      <button className="btn btn-acc full" disabled={busy}>{busy ? 'Einen Moment …' : 'Einloggen'}</button>
      <p className="alt"><a href="#" onClick={forgot}>Passwort vergessen?</a> · <Link href="/registrieren">Konto erstellen</Link></p>
    </form>
  );
}
