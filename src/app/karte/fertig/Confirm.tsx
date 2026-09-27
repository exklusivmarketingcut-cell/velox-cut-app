'use client';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

export default function Confirm({ id, next }: { id: string; next: string }) {
  const router = useRouter();
  const [err, setErr] = useState('');
  useEffect(() => {
    fetch('/api/stripe/card-saved', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ setupIntentId: id }) }).then((r) => {
      if (r.ok) {
        router.replace(next);
        router.refresh();
      } else setErr('Die Karte konnte nicht bestätigt werden.');
    });
  }, [id, next, router]);
  return err ? <p className="err">{err}</p> : <p className="muted" style={{ textAlign: 'center' }}>Einen Moment.</p>;
}
