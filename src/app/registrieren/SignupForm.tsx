'use client';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { createClient } from '@/lib/supabase/client';

export default function SignupForm() {
  const router = useRouter();
  const [err, setErr] = useState('');
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const name = String(f.get('name') || '').trim();
    const email = String(f.get('email') || '').trim();
    const password = String(f.get('password') || '');
    if (!name || !email) return setErr('Bitte Name und E-Mail angeben.');
    if (password.length < 8) return setErr('Das Passwort muss mindestens 8 Zeichen haben.');
    if (!f.get('agb')) return setErr('Bitte bestätige die AGB und die Datenschutzerklärung.');
    setBusy(true);
    setErr('');
    const supabase = createClient();
    const { data, error } = await supabase.auth.signUp({
      email,
      password,
      options: { data: { name }, emailRedirectTo: `${location.origin}/auth/callback?weiter=/karte` },
    });
    setBusy(false);
    if (error) return setErr(error.message.includes('registered') ? 'Diese E-Mail hat schon ein Konto. Bitte einloggen.' : 'Das hat nicht geklappt. Bitte versuch es noch einmal.');
    if (data.session) {
      router.replace('/karte');
      router.refresh();
    }
    else setSent(true);
  }

  if (sent)
    return (
      <p className="ok" style={{ textAlign: 'center' }}>
        Fast geschafft! Wir haben dir eine E-Mail geschickt. Klick auf den Link darin, um dein Konto zu bestätigen.
      </p>
    );

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <label className="f">Vorname<input className="inp" name="name" autoComplete="given-name" required /></label>
      <label className="f">E-Mail<input className="inp" name="email" type="email" autoComplete="email" required /></label>
      <label className="f">Passwort (mind. 8 Zeichen)<input className="inp" name="password" type="password" autoComplete="new-password" minLength={8} required /></label>
      <label className="check">
        <input type="checkbox" name="agb" />
        <span>Ich akzeptiere die <a href="/agb.html" target="_blank">AGB</a> und habe die <a href="/datenschutz.html" target="_blank">Datenschutzerklärung</a> gelesen.</span>
      </label>
      {err ? <p className="err">{err}</p> : null}
      <button className="btn btn-acc full" disabled={busy}>{busy ? 'Einen Moment …' : 'Konto erstellen →'}</button>
      <p className="note">Im nächsten Schritt hinterlegst du deine Karte. Dabei wird nichts abgebucht.</p>
      <p className="alt">Schon ein Konto? <Link href="/login">Einloggen</Link></p>
    </form>
  );
}
