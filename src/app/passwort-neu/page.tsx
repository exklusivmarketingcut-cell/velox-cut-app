'use client';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import AuthShell from '@/components/AuthShell';
import { createClient } from '@/lib/supabase/client';

export default function Page() {
  const router = useRouter();
  const [err, setErr] = useState('');
  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const pw = String(new FormData(e.currentTarget).get('password') || '');
    if (pw.length < 8) return setErr('Mindestens 8 Zeichen.');
    const { error } = await createClient().auth.updateUser({ password: pw });
    if (error) return setErr('Der Link ist abgelaufen. Bitte fordere einen neuen an.');
    router.replace('/studio');
    router.refresh();
  }
  return (
    <AuthShell title="Neues Passwort">
      <form className="stack" onSubmit={submit}>
        <label className="f">Neues Passwort<input className="inp" name="password" type="password" autoComplete="new-password" required /></label>
        {err ? <p className="err">{err}</p> : null}
        <button className="btn btn-acc full">Speichern</button>
      </form>
    </AuthShell>
  );
}
