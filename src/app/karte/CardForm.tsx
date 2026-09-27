'use client';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';
import { loadStripe } from '@stripe/stripe-js';
import { Elements, PaymentElement, useElements, useStripe } from '@stripe/react-stripe-js';

const appearance = {
  theme: 'night' as const,
  variables: { colorPrimary: '#4F9EFF', colorBackground: '#0b0d13', borderRadius: '12px', fontFamily: 'Inter, system-ui, sans-serif' },
};

export default function CardForm({ publishableKey }: { publishableKey: string }) {
  const stripePromise = useMemo(() => loadStripe(publishableKey), [publishableKey]);
  const [secret, setSecret] = useState('');
  const [err, setErr] = useState('');
  useEffect(() => {
    fetch('/api/stripe/setup-intent', { method: 'POST' })
      .then((r) => r.json())
      .then((d) => (d.clientSecret ? setSecret(d.clientSecret) : setErr('Die Kartenerfassung konnte nicht geladen werden.')))
      .catch(() => setErr('Die Kartenerfassung konnte nicht geladen werden.'));
  }, []);
  if (err) return <p className="err">{err}</p>;
  if (!secret) return <p className="muted" style={{ textAlign: 'center' }}>Lädt …</p>;
  return (
    <Elements stripe={stripePromise} options={{ clientSecret: secret, appearance, locale: 'de' }}>
      <Inner />
    </Elements>
  );
}

function Inner() {
  const router = useRouter();
  const stripe = useStripe();
  const elements = useElements();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!stripe || !elements) return;
    setBusy(true);
    setErr('');
    const { error, setupIntent } = await stripe.confirmSetup({
      elements,
      confirmParams: { return_url: `${location.origin}/karte/fertig` },
      redirect: 'if_required',
    });
    if (error || !setupIntent) {
      setBusy(false);
      return setErr(error?.message || 'Die Karte konnte nicht gespeichert werden.');
    }
    const r = await fetch('/api/stripe/card-saved', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ setupIntentId: setupIntent.id }) });
    if (!r.ok) {
      setBusy(false);
      return setErr('Die Karte konnte nicht bestätigt werden. Bitte versuch es noch einmal.');
    }
    router.replace('/studio?willkommen=1');
    router.refresh();
  }

  return (
    <form className="stack" onSubmit={submit}>
      <PaymentElement options={{ layout: 'tabs' }} />
      {err ? <p className="err">{err}</p> : null}
      <button className="btn btn-acc full" disabled={busy || !stripe}>{busy ? 'Wird gespeichert …' : 'Karte speichern – 0 €'}</button>
      <p className="note">Deine Kartendaten gehen direkt an unseren Zahlungsdienstleister Stripe und werden nicht bei uns gespeichert.</p>
    </form>
  );
}
