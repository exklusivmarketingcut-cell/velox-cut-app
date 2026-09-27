'use client';
import { usePathname, useRouter } from 'next/navigation';
import { useState } from 'react';
import type { Interval, PlanId } from '@/lib/pricing';

export type PickerPlan = { id: PlanId; name: string; credits: number; monthlyTxt: string; yearlyTxt: string };

/** Paket direkt auf der Registrierungsseite wechseln (Aufklapp-Menü statt zurück zur Website) */
export default function PlanPicker({ plans, paket, intervall }: { plans: PickerPlan[]; paket: PlanId; intervall: Interval }) {
  const router = useRouter();
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const cur = plans.find((p) => p.id === paket) ?? plans[0];
  const price = (p: PickerPlan, iv: Interval) => (p.id === 'free' ? '0 €' : iv === 'yearly' ? `${p.yearlyTxt} / Jahr` : `${p.monthlyTxt} / Monat`);
  const videos = (p: PickerPlan) => (p.id === 'free' ? '≈ 1 Video' : `bis zu ${p.credits / 10} Videos / Monat`);

  function choose(id: PlanId, iv: Interval, close = true) {
    const q = id === 'free' ? '' : `?paket=${id}&intervall=${iv}`;
    router.replace(`${path}${q}`, { scroll: false });
    if (close) setOpen(false);
  }

  return (
    <div className={`picker${open ? ' open' : ''}`}>
      <button type="button" className="choice" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <div>
          <b>{cur.name}</b>
          <span>{price(cur, intervall)} · {cur.credits} Credits{cur.id === 'free' ? ' (≈ 1 Video)' : ' pro Monat'}</span>
        </div>
        <svg className="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
        <span className="sr-only">Paket ändern</span>
      </button>
      {open ? (
        <div className="picker-list" role="listbox" aria-label="Paket wählen">
          <div className="tabs picker-iv">
            <button type="button" className={intervall === 'monthly' ? 'on' : ''} onClick={() => choose(paket === 'free' ? 'pro' : paket, 'monthly', false)}>Monatlich</button>
            <button type="button" className={intervall === 'yearly' ? 'on' : ''} onClick={() => choose(paket === 'free' ? 'pro' : paket, 'yearly', false)}>Jährlich · 2 Monate gratis</button>
          </div>
          {plans.map((p) => (
            <button key={p.id} type="button" role="option" aria-selected={p.id === paket} className={`picker-item${p.id === paket ? ' on' : ''}`} onClick={() => choose(p.id, intervall)}>
              <b>{p.name}{p.id === 'pro' ? <em>Beliebt</em> : null}</b>
              <span>{price(p, intervall)}</span>
              <small>{p.credits} Credits · {videos(p)}</small>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
