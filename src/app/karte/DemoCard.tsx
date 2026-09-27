'use client';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

// NUR im Demo-Durchlauf (NEXT_PUBLIC_DEMO=1): ersetzt das Stripe-Kartenfeld, das ohne echtes Stripe-Konto nicht lädt.
// Der Ablauf danach (SetupIntent prüfen, Karte als Standard setzen) ist derselbe wie live.
export default function DemoCard() {
  const router = useRouter();
  const [secret, setSecret] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  useEffect(() => {
    fetch('/api/stripe/setup-intent', { method: 'POST' })
      .then((r) => r.json())
      .then((d) => (d.clientSecret ? setSecret(d.clientSecret) : setErr('Die Kartenerfassung konnte nicht geladen werden.')));
  }, []);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setErr('');
    const r = await fetch(`${process.env.NEXT_PUBLIC_DEMO_STRIPE}/demo/confirm_setup`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ client_secret: secret, number: f.get('number'), exp: f.get('exp'), cvc: f.get('cvc') }),
    });
    const d = await r.json();
    if (!r.ok) { setBusy(false); return setErr(d.error?.message || 'Die Karte konnte nicht gespeichert werden.'); }
    const ok = await fetch('/api/stripe/card-saved', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ setupIntentId: d.setupIntent.id }) });
    if (!ok.ok) { setBusy(false); return setErr('Die Karte konnte nicht bestätigt werden. Bitte versuch es noch einmal.'); }
    router.replace('/studio?willkommen=1');
    router.refresh();
  }

  if (!secret && !err) return <p className="muted" style={{ textAlign: 'center' }}>Lädt …</p>;
  return (
    <form className="stack" onSubmit={submit} noValidate>
      <div className="tabs" style={{ margin: 0 }}>
        <button type="button" className="on">💳 Karte</button>
        <button type="button" disabled title="Im Demo nicht verfügbar"> Pay</button>
        <button type="button" disabled title="Im Demo nicht verfügbar">G Pay</button>
      </div>
      <label className="f">Kartennummer<input className="inp" name="number" inputMode="numeric" autoComplete="cc-number" placeholder="1234 1234 1234 1234" /></label>
      <div className="row">
        <label className="f" style={{ flex: 1 }}>Gültig bis<input className="inp" name="exp" autoComplete="cc-exp" placeholder="MM / JJ" /></label>
        <label className="f" style={{ flex: 1 }}>Sicherheitscode<input className="inp" name="cvc" inputMode="numeric" autoComplete="cc-csc" placeholder="CVC" /></label>
      </div>
      {err ? <p className="err">{err}</p> : null}
      <button className="btn btn-acc full" disabled={busy || !secret}>{busy ? 'Wird gespeichert …' : 'Karte speichern – 0 €'}</button>
      <p className="note">Deine Kartendaten gehen direkt an unseren Zahlungsdienstleister Stripe und werden nicht bei uns gespeichert.</p>
      <p className="note" style={{ color: '#fde68a' }}>Demo: nur Stripe-Testkarten, z. B. 4242 4242 4242 4242 (klappt) oder 4000 0000 0000 0002 (abgelehnt).</p>
    </form>
  );
}
