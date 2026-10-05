'use client';
import { useState } from 'react';

/** Freunde werben: persönlicher Link + Kopieren/Teilen */
export default function Invite({ link, werber, freund, count }: { link: string; werber: number; freund: number; count: number }) {
  const [done, setDone] = useState(false);
  async function share() {
    const text = `Ich schneide meine Reels mit Velox Cut – Rohmaterial rein, fertiges Video raus. Mit meinem Link bekommst du ${freund} Bonus-Credits: ${link}`;
    try {
      if (navigator.share) await navigator.share({ title: 'Velox Cut', text, url: link });
      else {
        await navigator.clipboard.writeText(link);
        setDone(true);
        setTimeout(() => setDone(false), 2000);
      }
    } catch { /* abgebrochen */ }
  }
  return (
    <section className="card stack">
      <h2 style={{ margin: 0 }}>🎁 Freunde einladen</h2>
      <p className="muted" style={{ margin: 0 }}>
        Kauft jemand über deinen Link sein erstes Paket oder Credits, bekommst du <b>{werber} Credits</b> (bis zu {werber / 10} Videos) und dein Freund <b>{freund} Credits</b> obendrauf.
      </p>
      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
        <input className="inp" readOnly value={link} onFocus={(e) => e.currentTarget.select()} style={{ flex: '1 1 260px', minWidth: 0 }} aria-label="Dein Einladungslink" />
        <button type="button" className="btn btn-acc" onClick={share} style={{ padding: '10px 18px', fontSize: 14 }}>{done ? '✓ Kopiert' : 'Link teilen'}</button>
      </div>
      {count ? <p className="note" style={{ margin: 0 }}>Bisher: {count} {count === 1 ? 'Freund hat' : 'Freunde haben'} gekauft – danke!</p> : null}
    </section>
  );
}
