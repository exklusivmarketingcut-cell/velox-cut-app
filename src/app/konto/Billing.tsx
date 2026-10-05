'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { loadStripe } from '@stripe/stripe-js';

type Plan = { id: 'starter' | 'pro' | 'business'; name: string; credits: number; monthlyTxt: string; yearlyTxt: string };
type Pack = { id: 'p100' | 'p300' | 'p600'; credits: number; priceTxt: string };

const CONSENT = 'Ich verlange ausdrücklich, dass Velox Cut sofort mit der Leistung beginnt. Mir ist bekannt, dass ich bei einem Widerruf die bis dahin erbrachte Leistung anteilig bezahle.';

type CodeInfo = { code: string; art: 'credits' | 'gratis_monat'; credits: number; plan: Plan['id'] | null; planName: string | null; text: string };

export default function Billing({ current, hasSub, plans, packs, publishableKey, choice, initialCode }: {
  current: string; hasSub: boolean; plans: Plan[]; packs: Pack[]; publishableKey: string;
  /** Gutschein-Code aus dem Link (?code=…) oder von der Registrierung */
  initialCode?: string;
  /** Paket, das auf der Website gewählt wurde (letzter Schritt nach der Anmeldung) */
  choice?: { plan: Plan['id']; interval: 'monthly' | 'yearly' } | null;
}) {
  const stripePromise = useMemo(() => loadStripe(publishableKey), [publishableKey]);
  const [interval, setInterval] = useState<'monthly' | 'yearly'>(choice?.interval ?? 'monthly');
  const [plan, setPlan] = useState<Plan['id']>(choice?.plan ?? 'pro');
  const aboRef = useRef<HTMLElement>(null);
  const focus = !!choice && !hasSub;
  useEffect(() => {
    if (focus) aboRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [focus]);
  const [pack, setPack] = useState<Pack['id']>('p100');
  const [c1, setC1] = useState(false);
  const [c2, setC2] = useState(false);
  const [busy, setBusy] = useState('');
  // Meldungen direkt unter dem jeweiligen Button anzeigen
  const [msg, setMsg] = useState<{ at?: 'pack' | 'sub' | 'code'; ok?: string; err?: string }>({});
  const [codeIn, setCodeIn] = useState(initialCode ?? '');
  const [code, setCode] = useState<CodeInfo | null>(null);
  const trial = !!code && code.art === 'gratis_monat' && code.plan === plan && interval === 'monthly';

  async function post(url: string, body: object) {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    return { ok: r.ok, d: await r.json().catch(() => ({})) };
  }

  async function checkCode(raw = codeIn) {
    if (!raw.trim()) return;
    setBusy('code'); setMsg({});
    const { ok, d } = await post('/api/codes/pruefen', { code: raw });
    setBusy('');
    if (!ok) { setCode(null); return setMsg({ at: 'code', err: d.error || 'Code nicht gefunden.' }); }
    setCode(d);
    if (d.art === 'gratis_monat' && d.plan) {
      setPlan(d.plan); setInterval('monthly');
      setTimeout(() => aboRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
    }
  }
  useEffect(() => { if (initialCode) checkCode(initialCode); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function redeem() {
    setBusy('code'); setMsg({});
    const { ok, d } = await post('/api/codes/einloesen', { code: code?.code });
    if (!ok) { setBusy(''); return setMsg({ at: 'code', err: d.error || 'Das hat nicht geklappt.' }); }
    setMsg({ at: 'code', ok: `${d.credits} Credits gutgeschrieben! 🎉` });
    setTimeout(() => location.reload(), 1500);
  }

  async function subscribe() {
    if (!c1) return setMsg({ at: 'sub', err: 'Bitte bestätige den sofortigen Leistungsbeginn.' });
    setBusy('sub'); setMsg({});
    const { ok, d } = await post('/api/stripe/subscribe', { plan, interval, consent: true, ...(trial ? { code: code!.code } : {}) });
    if (!ok) { setBusy(''); return setMsg({ at: 'sub', err: d.error || 'Das hat nicht geklappt.' }); }
    if (d.clientSecret) {
      const stripe = await stripePromise;
      const res = await stripe!.confirmCardPayment(d.clientSecret, { payment_method: d.paymentMethod });
      if (res.error) { setBusy(''); return setMsg({ at: 'sub', err: res.error.message || 'Die Zahlung wurde nicht bestätigt.' }); }
    }
    setMsg({ at: 'sub', ok: d.trial ? 'Gratis-Monat gestartet! Deine Credits sind in wenigen Sekunden da.' : 'Abo gestartet! Deine Credits sind in wenigen Sekunden da.' });
    setTimeout(() => location.reload(), 2500);
  }

  async function buy() {
    if (!c2) return setMsg({ at: 'pack', err: 'Bitte bestätige den sofortigen Leistungsbeginn.' });
    setBusy('pack'); setMsg({});
    const { ok, d } = await post('/api/stripe/topup', { pack, consent: true });
    if (!ok) { setBusy(''); return setMsg({ at: 'pack', err: d.error || 'Die Zahlung wurde abgelehnt.' }); }
    if (d.clientSecret) {
      const stripe = await stripePromise;
      const res = await stripe!.handleNextAction({ clientSecret: d.clientSecret });
      if (res.error) { setBusy(''); return setMsg({ at: 'pack', err: res.error.message || 'Die Zahlung wurde nicht bestätigt.' }); }
      await post('/api/stripe/topup-confirm', { id: d.id });
    }
    setMsg({ at: 'pack', ok: 'Credits gutgeschrieben!' });
    setTimeout(() => location.reload(), 1200);
  }

  return (
    <>
      <section className="card stack" id="code">
        <h2 style={{ font: '700 20px var(--display)' }}>Gutschein-Code</h2>
        <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
          <input className="inp" value={codeIn} onChange={(e) => { setCodeIn(e.target.value.toUpperCase()); setCode(null); }} placeholder="z. B. GASTRO"
            onKeyDown={(e) => e.key === 'Enter' && checkCode()} aria-label="Gutschein-Code" style={{ flex: '1 1 200px', minWidth: 0, textTransform: 'uppercase' }} />
          <button type="button" className="btn" onClick={() => checkCode()} disabled={!!busy || !codeIn.trim()} style={{ padding: '10px 18px', fontSize: 14 }}>{busy === 'code' ? 'Prüft …' : 'Prüfen'}</button>
        </div>
        {code ? (
          <div className="code-ok">
            <b>✓ {code.code}:</b> {code.text}
            {code.art === 'credits' ? (
              <div><button type="button" className="btn btn-acc" onClick={redeem} disabled={!!busy} style={{ padding: '9px 18px', fontSize: 14, marginTop: 8 }}>{busy === 'code' ? 'Löst ein …' : 'Jetzt einlösen'}</button></div>
            ) : hasSub ? (
              <p className="note" style={{ margin: '6px 0 0' }}>Du hast schon ein Abo – ein Gratis-Monat gilt nur für neue Abos.</p>
            ) : (
              <p className="note" style={{ margin: '6px 0 0' }}>Das Paket ist unten schon ausgewählt – Häkchen setzen und starten.</p>
            )}
          </div>
        ) : null}
        <Note m={msg} at="code" />
      </section>

      <section className="card stack" id="nachkauf">
        <h2 style={{ font: '700 20px var(--display)' }}>Credits nachkaufen</h2>
        <p className="muted" style={{ marginTop: -8 }}>Mit einem Klick über deine hinterlegte Karte. Nachgekaufte Credits verfallen nie.</p>
        <div className="packs">
          {packs.map((x) => (
            <button key={x.id} type="button" className={`pack${pack === x.id ? ' on' : ''}`} onClick={() => setPack(x.id)}>
              <b>{x.credits} Credits</b><span>bis zu {x.credits / 10} Videos (Clean)</span><em>{x.priceTxt}</em>
            </button>
          ))}
        </div>
        <label className="check"><input type="checkbox" checked={c2} onChange={(e) => setC2(e.target.checked)} /><span>{CONSENT}</span></label>
        <button className="btn btn-acc" type="button" onClick={buy} disabled={!!busy}>{busy === 'pack' ? 'Zahlung läuft …' : 'Jetzt zahlungspflichtig kaufen'}</button>
        <Note m={msg} at="pack" />
      </section>

      <section className={`card stack${focus ? ' focus' : ''}`} id="abo" ref={aboRef}>
        {focus ? <p className="ok" style={{ margin: 0 }}>Letzter Schritt: Paket prüfen, Häkchen setzen und bestätigen. Abgebucht wird erst jetzt.</p> : null}
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h2 style={{ font: '700 20px var(--display)' }}>{hasSub ? 'Dein Abo' : 'Paket wählen'}</h2>
          {!hasSub ? (
            <div className="tabs" style={{ margin: 0 }}>
              <button type="button" className={interval === 'monthly' ? 'on' : ''} onClick={() => setInterval('monthly')}>Monatlich</button>
              <button type="button" className={interval === 'yearly' ? 'on' : ''} onClick={() => setInterval('yearly')}>Jährlich · 2 Monate gratis</button>
            </div>
          ) : null}
        </div>
        {hasSub ? (
          <p className="muted">Paket wechseln, Karte ändern und Rechnungen findest du im Kundenbereich.</p>
        ) : (
          <>
            <div className="plans">
              {plans.map((x) => (
                <button key={x.id} type="button" className={`plan${plan === x.id ? ' cur' : ''}`} onClick={() => setPlan(x.id)} style={{ textAlign: 'left' }}>
                  <h3>{x.name}</h3>
                  <div className="price">{interval === 'monthly' ? x.monthlyTxt : x.yearlyTxt}<small> / {interval === 'monthly' ? 'Monat' : 'Jahr'}</small></div>
                  <span className="muted">{x.credits} Credits pro Monat · bis zu {x.credits / 10} Videos</span>
                </button>
              ))}
            </div>
            <label className="check"><input type="checkbox" checked={c1} onChange={(e) => setC1(e.target.checked)} /><span>{CONSENT}</span></label>
            {trial ? (
              <p className="ok" style={{ margin: 0 }}>🎁 Mit {code!.code}: der 1. Monat kostet <b>0 €</b>. Danach {plans.find((x) => x.id === plan)?.monthlyTxt} / Monat – du kannst jederzeit vorher kündigen, dann zahlst du nichts.</p>
            ) : null}
            <button className="btn btn-acc" type="button" onClick={subscribe} disabled={!!busy || current === plan}>{busy === 'sub' ? 'Zahlung läuft …' : trial ? 'Jetzt zahlungspflichtig abonnieren – 1. Monat 0 €' : 'Jetzt zahlungspflichtig abonnieren'}</button>
            <Note m={msg} at="sub" />
            <p className="note">Beta-Preisgarantie: Dein Preis bleibt, solange dein Abo läuft. Monatsabos sind monatlich kündbar.</p>
          </>
        )}
        <form action="/api/stripe/portal" method="post"><button className="btn">Rechnungen &amp; Abo verwalten</button></form>
      </section>
    </>
  );
}

function Note({ m, at }: { m: { at?: string; ok?: string; err?: string }; at: string }) {
  if (m.at !== at) return null;
  return m.err ? <p className="err" role="alert">{m.err}</p> : m.ok ? <p className="ok" role="status">{m.ok}</p> : null;
}
