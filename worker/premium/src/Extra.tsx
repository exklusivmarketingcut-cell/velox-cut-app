// Velox Cut – zusätzliche Premium-Bausteine (17.10.2026).
// Vorbilder: Yacht-Video der Webseite (3D-Titel, Maßlinie, Ortskarte mit Koordinaten, Tages-Kacheln, Funken, Filmlook)
// und die TikTok-Vorbilder des Inhabers (Überschrift im Farbkasten, durchgestrichene Liste, Prozent-Ring, Ticket-Karte,
// Button mit Mauszeiger, Profilkarte, Ablauf mit Knoten).
import React from 'react';
import {AbsoluteFill} from 'remotion';

export type XEv = {type: string; a: number; b: number; [k: string]: any};
export type Farben = {gold: string; goldL: string};

const clamp = (x: number, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const eo = (x: number) => 1 - Math.pow(1 - clamp(x), 3);
const eio = (x: number) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
const back = (x: number) => {
  const c = clamp(x);
  const k = 1.7;
  return 1 + (k + 1) * Math.pow(c - 1, 3) + k * Math.pow(c - 1, 2);
};
const fade = (t: number, a: number, b: number, dIn = 0.18, dOut = 0.2) => clamp((t - a) / dIn) * (1 - clamp((t - (b - dOut)) / dOut));
const rnd = (i: number) => {
  const x = Math.sin(i * 12.9898 + 78.233) * 43758.5453;
  return x - Math.floor(x);
};
const fmt = (n: number) => Math.round(n).toLocaleString('de-DE');
const INK = 'rgba(16,16,18,0.88)';
const STAGE: React.CSSProperties = {position: 'absolute', left: 60, top: 250, width: 960, height: 260, display: 'flex', alignItems: 'center', justifyContent: 'center'};
// Breite einer Zeile grob schätzen (Montserrat 900, Großbuchstaben ≈ 0,8 em) → Schrift so, dass sie in `max` px passt
const passt = (txt: string, fs: number, max: number, em = 0.8) => Math.min(fs, max / Math.max(1, String(txt).length * em));

/* ---------- Funken: kleine Partikel, die von einem Punkt wegsprühen ---------- */
export const Funken: React.FC<{t: number; at: number; x: number; y: number; n?: number; farbe: string; weite?: number}> = ({t, at, x, y, n = 26, farbe, weite = 320}) => {
  const u = (t - at) / 1.1;
  if (u < 0 || u > 1) return null;
  return (
    <>
      {Array.from({length: n}).map((_, i) => {
        const w = rnd(i) * Math.PI * 2;
        const r = (0.35 + 0.65 * rnd(i + 50)) * weite * eo(u);
        const s = 4 + 7 * rnd(i + 99);
        return (
          <div key={i} style={{position: 'absolute', left: x + Math.cos(w) * r - s / 2, top: y + Math.sin(w) * r * 0.75 + 140 * u * u - s / 2, width: s, height: s, borderRadius: s,
            background: i % 3 ? farbe : '#fff', opacity: (1 - u) * (0.6 + 0.4 * rnd(i + 7)), boxShadow: `0 0 ${s * 2}px ${farbe}`}} />
        );
      })}
    </>
  );
};

/* ---------- 3D-Titel mit Unterzeile (Yacht: „SPLIT · KROATIEN · ADRIA“) ---------- */
export const Titel3D: React.FC<{t: number; e: XEv; f: Farben}> = ({t, e, f}) => {
  if (t < e.a || t >= e.b) return null;
  const o = fade(t, e.a, e.b, 0.12, 0.25);
  const k = eo((t - e.a) / 0.75);
  const sub = eo((t - e.a - 0.45) / 0.5);
  const drift = 1 + 0.05 * clamp((t - e.a) / Math.max(0.6, e.b - e.a));
  const fs = e.size || passt(e.text, 230, 940, 0.86);
  return (
    <div style={{position: 'absolute', left: 0, right: 0, top: e.y || 250, display: 'flex', flexDirection: 'column', alignItems: 'center', opacity: o, perspective: 900}}>
      <div style={{fontFamily: 'Montserrat', fontWeight: 900, fontSize: fs, lineHeight: 0.95, whiteSpace: 'nowrap', letterSpacing: (1 - k) * 24,
        transform: `rotateX(${(1 - k) * 68}deg) translateY(${(1 - k) * 80}px) scale(${(0.8 + 0.2 * k) * drift})`, transformOrigin: '50% 100%',
        backgroundImage: `linear-gradient(180deg, #ffffff 30%, ${f.goldL} 75%, ${f.gold})`, WebkitBackgroundClip: 'text', color: 'transparent',
        filter: `drop-shadow(0 3px 0 ${f.gold}) drop-shadow(0 7px 0 rgba(0,0,0,0.35)) drop-shadow(0 14px 26px rgba(0,0,0,0.6))`}}>{e.text}</div>
      {e.sub ? (
        <div style={{display: 'flex', alignItems: 'center', gap: 18, marginTop: 14, opacity: sub}}>
          <div style={{height: 2, width: 90 * sub, background: f.gold}} />
          <div style={{fontFamily: 'Poppins', fontWeight: 600, fontSize: 26, letterSpacing: 10 + (1 - sub) * 20, color: '#fff', textTransform: 'uppercase', whiteSpace: 'nowrap'}}>{e.sub}</div>
          <div style={{height: 2, width: 90 * sub, background: f.gold}} />
        </div>
      ) : null}
      <div style={{position: 'absolute', left: 0, top: 0, width: 1080, height: 400}}>
        <Funken t={t} at={e.a + 0.55} x={540} y={fs * 0.5} farbe={f.goldL} />
      </div>
    </div>
  );
};

/* ---------- Maßlinie mit Zahl (Yacht: „48 M“) ---------- */
export const Mass: React.FC<{t: number; e: XEv; f: Farben}> = ({t, e, f}) => {
  if (t < e.a || t >= e.b) return null;
  const o = fade(t, e.a, e.b);
  const L = eo((t - e.a) / 0.55);
  const z = eo((t - e.a - 0.15) / 0.7);
  const w = 760 * L;
  return (
    <div style={{...STAGE, flexDirection: 'column', opacity: o}}>
      <div style={{fontFamily: 'Montserrat', fontWeight: 900, fontSize: 110, color: '#fff', fontStyle: 'italic', whiteSpace: 'nowrap', textShadow: `0 0 40px ${f.gold}66, 0 8px 24px rgba(0,0,0,0.7)`, fontVariantNumeric: 'tabular-nums'}}>
        {fmt((e.from || 0) + (e.to - (e.from || 0)) * z)}{e.suffix || ''}
      </div>
      <div style={{position: 'relative', width: 760, height: 40, marginTop: 6}}>
        <div style={{position: 'absolute', left: 380 - w / 2, top: 19, width: w, height: 3, background: '#fff', boxShadow: `0 0 12px ${f.gold}`}} />
        <div style={{position: 'absolute', left: 380 - w / 2 - 1, top: 4, width: 3, height: 32, background: '#fff', opacity: L}} />
        <div style={{position: 'absolute', left: 380 + w / 2 - 2, top: 4, width: 3, height: 32, background: '#fff', opacity: L}} />
      </div>
      {e.label ? <div style={{fontFamily: 'Montserrat', fontWeight: 900, fontSize: 46, marginTop: 14, whiteSpace: 'nowrap', color: '#fff', opacity: eo((t - e.a - 0.5) / 0.4), textShadow: '0 4px 18px rgba(0,0,0,0.8)'}}>
        {e.label.split(' ').map((x: string, i: number) => <span key={i} style={{color: i % 2 ? f.goldL : '#fff'}}>{x} </span>)}
      </div> : null}
    </div>
  );
};

/* ---------- Ortskarte mit Pin, Name wird getippt, Koordinaten darunter (Yacht) ---------- */
export const Ort: React.FC<{t: number; e: XEv; f: Farben}> = ({t, e, f}) => {
  if (t < e.a || t >= e.b) return null;
  const o = fade(t, e.a, e.b);
  const p = back((t - e.a) / 0.4);
  const n = Math.floor(clamp((t - e.a - 0.2) / 0.7) * e.text.length);
  const kn = Math.floor(clamp((t - e.a - 0.7) / 0.7) * (e.sub || '').length);
  return (
    <div style={{position: 'absolute', left: 56, top: e.y || 300, opacity: o, transform: `translateX(${(1 - clamp(p)) * -60}px) scale(${0.9 + 0.1 * clamp(p)})`, transformOrigin: '0 50%'}}>
      <div style={{display: 'flex', alignItems: 'center', gap: 18, background: INK, borderRadius: 22, padding: '16px 28px 16px 18px', border: `1.5px solid ${f.gold}66`, boxShadow: '0 14px 40px rgba(0,0,0,0.55)'}}>
        <div style={{width: 46, height: 46, borderRadius: 23, background: `radial-gradient(circle at 35% 35%, #ff7a8a, #e0294a)`, display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 0 0 6px rgba(224,41,74,0.18)'}}>
          <div style={{width: 14, height: 14, borderRadius: 7, background: '#fff'}} />
        </div>
        <div>
          <div style={{fontFamily: 'Montserrat', fontWeight: 900, fontSize: 34, color: '#fff', textTransform: 'uppercase', letterSpacing: 1, whiteSpace: 'nowrap'}}>{e.text.slice(0, n)}<span style={{opacity: n < e.text.length ? 1 : 0}}>|</span></div>
          {e.sub ? <div style={{fontFamily: 'Poppins', fontWeight: 600, fontSize: 19, color: 'rgba(255,255,255,0.7)', letterSpacing: 2, marginTop: 4, whiteSpace: 'nowrap'}}>{e.sub.slice(0, kn)}</div> : null}
        </div>
      </div>
    </div>
  );
};

/* ---------- Tages-Kacheln (Yacht: „1 WOCHE · MO DI MI … GEMIETET“) ---------- */
export const Tage: React.FC<{t: number; e: XEv; f: Farben}> = ({t, e, f}) => {
  if (t < e.a || t >= e.b) return null;
  const o = fade(t, e.a, e.b);
  const tage: string[] = e.items && e.items.length ? e.items : ['MO', 'DI', 'MI', 'DO', 'FR', 'SA', 'SO'];
  const span = Math.max(0.6, Math.min(1.6, e.b - e.a - 0.8));
  const h = back((t - e.a) / 0.4);
  return (
    <div style={{...STAGE, flexDirection: 'column', opacity: o}}>
      <div style={{fontFamily: 'Montserrat', fontWeight: 900, fontSize: passt(e.text, 96, 900), color: '#fff', whiteSpace: 'nowrap', transform: `scale(${0.85 + 0.15 * clamp(h)})`, textShadow: '0 8px 30px rgba(0,0,0,0.7)'}}>{e.text}</div>
      <div style={{display: 'flex', gap: 12, marginTop: 18}}>
        {tage.map((d, i) => {
          const at = e.a + 0.35 + (i * span) / tage.length;
          const p = back((t - at) / 0.3);
          return (
            <div key={i} style={{width: 92, height: 92, borderRadius: 20, background: `linear-gradient(160deg, ${f.goldL}, ${f.gold})`, opacity: clamp((t - at) / 0.1),
              transform: `scale(${t >= at ? 0.6 + 0.4 * clamp(p) : 0.6}) translateY(${(1 - clamp(p)) * 20}px)`, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', boxShadow: `0 8px 24px ${f.gold}55`}}>
              <div style={{fontFamily: 'Montserrat', fontWeight: 900, fontSize: 24, color: '#141210'}}>{d}</div>
              <div style={{fontFamily: 'Montserrat', fontWeight: 900, fontSize: 22, color: '#141210', marginTop: 2}}>✓</div>
            </div>
          );
        })}
      </div>
      {e.sub ? <div style={{fontFamily: 'Poppins', fontWeight: 600, fontSize: 24, letterSpacing: 8, color: '#fff', marginTop: 16, opacity: eo((t - e.a - 0.35 - span) / 0.4), textTransform: 'uppercase'}}>{e.sub}</div> : null}
    </div>
  );
};

/* ---------- Überschrift im Farbkasten (TikToks: „Das Wichtigste:“, „UND DAS KRASSE:“) ---------- */
export const Kasten: React.FC<{t: number; e: XEv; f: Farben}> = ({t, e, f}) => {
  if (t < e.a || t >= e.b) return null;
  const o = fade(t, e.a, e.b, 0.08, 0.18);
  const zeilen: string[] = String(e.text).split('\n');
  return (
    <div style={{...STAGE, flexDirection: 'column', gap: 8, opacity: o}}>
      {zeilen.map((z, i) => {
        const at = e.a + i * 0.18;
        const w = eio(clamp((t - at) / 0.28));
        const box = i === zeilen.length - 1 || zeilen.length === 1;
        return (
          <div key={i} style={{position: 'relative', padding: box ? '4px 22px' : '0 10px'}}>
            {box ? <div style={{position: 'absolute', left: 0, top: 0, bottom: 0, width: `${w * 100}%`, background: f.gold, borderRadius: 8, transform: 'skewX(-6deg)'}} /> : null}
            <div style={{position: 'relative', fontFamily: 'Montserrat', fontWeight: 900, fontSize: passt(z, e.size || 84, 900), color: '#fff', textTransform: 'uppercase', whiteSpace: 'nowrap', lineHeight: 1.05,
              opacity: clamp((t - at - 0.08) / 0.15), textShadow: box ? '0 3px 0 rgba(0,0,0,0.25)' : '0 6px 22px rgba(0,0,0,0.8)'}}>{z}</div>
          </div>
        );
      })}
    </div>
  );
};

/* ---------- Durchgestrichene Liste (Herr Tech: „Schluss mit … ✗“) ---------- */
export const Durch: React.FC<{t: number; e: XEv; f: Farben}> = ({t, e, f}) => {
  if (t < e.a || t >= e.b) return null;
  const o = fade(t, e.a, e.b);
  return (
    <div style={{...STAGE, flexDirection: 'column', alignItems: 'center', gap: 14, top: 250, opacity: o}}>
      {e.kicker ? <div style={{fontFamily: 'Montserrat', fontWeight: 900, fontSize: 64, color: '#fff', textTransform: 'uppercase', whiteSpace: 'nowrap', textShadow: '0 6px 20px rgba(0,0,0,0.8)'}}>{e.kicker}</div> : null}
      {e.items.map((it: any, i: number) => {
        const p = back((t - it.a) / 0.3);
        const s = eio(clamp((t - it.a - 0.35) / 0.3));
        return (
          <div key={i} style={{display: 'flex', alignItems: 'center', gap: 14, opacity: clamp((t - it.a) / 0.12), transform: `translateX(${(1 - clamp(p)) * -40}px)`}}>
            <div style={{position: 'relative', background: INK, borderRadius: 14, padding: '10px 22px', border: '1.5px solid rgba(255,255,255,0.12)'}}>
              <div style={{fontFamily: 'Montserrat', fontWeight: 700, fontSize: 36, color: s > 0.5 ? 'rgba(255,255,255,0.5)' : '#fff', whiteSpace: 'nowrap'}}>{it.text}</div>
              <div style={{position: 'absolute', left: 16, top: '52%', height: 4, width: `calc(${s * 100}% - 32px)`, background: '#ff3b4e', borderRadius: 2}} />
            </div>
            <div style={{width: 46, height: 46, borderRadius: 23, background: '#ff3b4e', display: 'flex', alignItems: 'center', justifyContent: 'center', transform: `scale(${s})`,
              fontFamily: 'Montserrat', fontWeight: 900, fontSize: 28, color: '#fff'}}>✕</div>
          </div>
        );
      })}
    </div>
  );
};

/* ---------- Prozent-Ring (Instagram-Video: „83 %“) ---------- */
export const Ring: React.FC<{t: number; e: XEv; f: Farben}> = ({t, e, f}) => {
  if (t < e.a || t >= e.b) return null;
  const o = fade(t, e.a, e.b);
  const p = back((t - e.a) / 0.4);
  const u = eo((t - e.a - 0.2) / 0.9);
  const R = 92, U = 2 * Math.PI * R;
  return (
    <div style={{...STAGE, gap: 34, opacity: o}}>
      <div style={{position: 'relative', width: 230, height: 230, transform: `scale(${0.8 + 0.2 * clamp(p)})`}}>
        <svg width={230} height={230} style={{transform: 'rotate(-90deg)'}}>
          <circle cx={115} cy={115} r={R} stroke="rgba(255,255,255,0.15)" strokeWidth={22} fill="none" />
          <circle cx={115} cy={115} r={R} stroke={f.gold} strokeWidth={22} fill="none" strokeLinecap="round" strokeDasharray={`${U * (e.to / 100) * u} ${U}`} style={{filter: `drop-shadow(0 0 10px ${f.gold})`}} />
        </svg>
        <div style={{position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'Montserrat', fontWeight: 900, fontSize: 64, color: '#fff'}}>{Math.round(e.to * u)}%</div>
      </div>
      {e.label ? <div style={{fontFamily: 'Montserrat', fontWeight: 900, fontSize: 52, color: '#fff', maxWidth: 520, lineHeight: 1.05, opacity: eo((t - e.a - 0.3) / 0.4), textShadow: '0 6px 20px rgba(0,0,0,0.8)'}}>{e.label}</div> : null}
    </div>
  );
};

/* ---------- Ticket-/Datumskarte (Herr Tech: „KI POWER DAY · 17. OKTOBER“) ---------- */
export const Ticket: React.FC<{t: number; e: XEv; f: Farben}> = ({t, e, f}) => {
  if (t < e.a || t >= e.b) return null;
  const o = fade(t, e.a, e.b);
  const p = back((t - e.a) / 0.45);
  const wob = Math.sin((t - e.a) * 2.2) * 1.2;
  return (
    <div style={{...STAGE, opacity: o}}>
      <div style={{position: 'relative', width: 640, borderRadius: 26, overflow: 'hidden', background: '#fff', boxShadow: '0 30px 70px rgba(0,0,0,0.6)', transform: `translateY(${(1 - clamp(p)) * 120}px) rotate(${(1 - clamp(p)) * -8 + wob}deg) scale(${0.85 + 0.15 * clamp(p)})`}}>
        <div style={{background: `linear-gradient(120deg, ${f.gold}, ${f.goldL})`, padding: '14px 30px', fontFamily: 'Montserrat', fontWeight: 900, fontSize: passt(e.kicker || '', 34, 560), color: '#141210', textTransform: 'uppercase', textAlign: 'center', whiteSpace: 'nowrap'}}>{e.kicker}</div>
        <div style={{padding: '18px 30px 24px', textAlign: 'center'}}>
          <div style={{fontFamily: 'Montserrat', fontWeight: 900, fontSize: passt(e.text, 60, 570), color: '#141210', textTransform: 'uppercase', whiteSpace: 'nowrap'}}>{e.text}</div>
          {e.sub ? <div style={{fontFamily: 'Poppins', fontWeight: 600, fontSize: 26, color: '#6b6457', marginTop: 8, whiteSpace: 'nowrap'}}>{e.sub}</div> : null}
        </div>
        <div style={{position: 'absolute', left: -22, top: 78, width: 44, height: 44, borderRadius: 22, background: '#0b0b0c'}} />
        <div style={{position: 'absolute', right: -22, top: 78, width: 44, height: 44, borderRadius: 22, background: '#0b0b0c'}} />
      </div>
    </div>
  );
};

/* ---------- Button mit Mauszeiger-Klick (Instagram-/KI-Videos: „Folgen“ → „Gefolgt ✓“) ---------- */
export const Klick: React.FC<{t: number; e: XEv; f: Farben}> = ({t, e, f}) => {
  if (t < e.a || t >= e.b) return null;
  const o = fade(t, e.a, e.b);
  const p = back((t - e.a) / 0.35);
  const ck = e.a + Math.min(1.0, (e.b - e.a) * 0.45);
  const m = eio(clamp((t - e.a - 0.15) / (ck - e.a - 0.15)));
  const done = t >= ck;
  const ripple = clamp((t - ck) / 0.45);
  const druck = done ? 1 - 0.08 * Math.max(0, 1 - (t - ck) / 0.12) : 1;
  return (
    <div style={{...STAGE, opacity: o}}>
      <div style={{position: 'relative', transform: `scale(${(0.8 + 0.2 * clamp(p)) * druck})`}}>
        <div style={{padding: '30px 96px', borderRadius: 80, background: done ? INK : `linear-gradient(120deg, ${f.gold}, ${f.goldL})`, border: done ? `2px solid ${f.gold}` : 'none',
          fontFamily: 'Montserrat', fontWeight: 900, fontSize: 72, color: done ? '#fff' : '#141210', whiteSpace: 'nowrap', boxShadow: `0 14px 40px ${f.gold}44`}}>
          {done ? `${e.done || e.text} ✓` : e.text}
        </div>
        {done ? <div style={{position: 'absolute', left: '50%', top: '50%', width: 320 * ripple, height: 320 * ripple, marginLeft: -160 * ripple, marginTop: -160 * ripple, borderRadius: '50%', border: `3px solid ${f.goldL}`, opacity: 1 - ripple}} /> : null}
        <svg width={64} height={64} viewBox="0 0 24 24" style={{position: 'absolute', left: 260 - 120 * m + 40, top: 150 - 110 * m + 30, filter: 'drop-shadow(0 4px 8px rgba(0,0,0,0.6))', transform: `scale(${done && t - ck < 0.12 ? 0.85 : 1})`}}>
          <path d="M4 2 L4 20 L9 15 L12.5 22 L15.5 20.5 L12 13.5 L19 13.5 Z" fill="#fff" stroke="#141210" strokeWidth={1.4} strokeLinejoin="round" />
        </svg>
      </div>
    </div>
  );
};

/* ---------- Profilkarte (Agentur-Video: Instagram-Profil mit Zahlen) ---------- */
export const Profil: React.FC<{t: number; e: XEv; f: Farben; logo?: string}> = ({t, e, f}) => {
  if (t < e.a || t >= e.b) return null;
  const o = fade(t, e.a, e.b);
  const p = back((t - e.a) / 0.45);
  const u = eo((t - e.a - 0.25) / 0.9);
  const stats: any[] = e.items || [];
  return (
    <div style={{...STAGE, opacity: o}}>
      <div style={{width: 760, borderRadius: 30, background: 'rgba(22,22,26,0.92)', border: `1.5px solid ${f.gold}55`, padding: '26px 30px', boxShadow: `0 24px 60px rgba(0,0,0,0.6), 0 0 60px ${f.gold}22`,
        transform: `translateY(${(1 - clamp(p)) * 60}px) scale(${0.88 + 0.12 * clamp(p)})`}}>
        <div style={{display: 'flex', alignItems: 'center', gap: 22}}>
          <div style={{width: 96, height: 96, borderRadius: 48, padding: 4, background: `conic-gradient(${f.gold}, #e0294a, ${f.goldL}, ${f.gold})`}}>
            <div style={{width: '100%', height: '100%', borderRadius: 44, background: '#141210', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'Montserrat', fontWeight: 900, fontSize: 38, color: f.goldL}}>{(e.kicker || e.text || '?').replace('@', '').slice(0, 1).toUpperCase()}</div>
          </div>
          <div>
            <div style={{fontFamily: 'Montserrat', fontWeight: 900, fontSize: 38, color: '#fff', whiteSpace: 'nowrap'}}>{e.text}</div>
            {e.kicker ? <div style={{fontFamily: 'Poppins', fontWeight: 600, fontSize: 22, color: 'rgba(255,255,255,0.65)', marginTop: 4, whiteSpace: 'nowrap'}}>{e.kicker}</div> : null}
          </div>
        </div>
        {stats.length ? (
          <div style={{display: 'flex', justifyContent: 'space-around', marginTop: 22}}>
            {stats.map((s, i) => (
              <div key={i} style={{textAlign: 'center'}}>
                <div style={{fontFamily: 'Montserrat', fontWeight: 900, fontSize: 40, color: '#fff', fontVariantNumeric: 'tabular-nums'}}>{typeof s.zahl === 'number' ? fmt(s.zahl * u) : s.zahl}</div>
                <div style={{fontFamily: 'Poppins', fontWeight: 600, fontSize: 20, color: 'rgba(255,255,255,0.6)'}}>{s.text}</div>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
};

/* ---------- Ablauf mit Knoten und Pfeilen (KI-Video: „Claude-Abo → HyperFrames → Video“) ---------- */
// Test 17.10. (Papaya): untereinander war der Ablauf zu hoch und lag auf den Untertiteln → nebeneinander in einer Zeile über dem Kopf.
export const Flow: React.FC<{t: number; e: XEv; f: Farben}> = ({t, e, f}) => {
  if (t < e.a || t >= e.b) return null;
  const o = fade(t, e.a, e.b);
  const items: any[] = (e.items || []).slice(0, 3);
  const fs = passt(items.map((x) => x.text).join('     '), 34, 860, 0.62);
  return (
    <div style={{...STAGE, top: e.y || 300, height: 200, opacity: o, gap: 0}}>
      {items.map((it, i) => {
        const p = back((t - it.a) / 0.35);
        const pfeil = i > 0 ? eio(clamp((t - it.a + 0.25) / 0.25)) : 0;
        return (
          <React.Fragment key={i}>
            {i > 0 ? <div style={{width: 46, display: 'flex', alignItems: 'center'}}><div style={{height: 4, width: 34 * pfeil, background: `linear-gradient(90deg, ${f.gold}, ${f.goldL})`, borderRadius: 2}} /><div style={{width: 0, height: 0, borderTop: '9px solid transparent', borderBottom: '9px solid transparent', borderLeft: `12px solid ${f.goldL}`, opacity: pfeil}} /></div> : null}
            <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', background: INK, borderRadius: 20, padding: '14px 20px', border: `1.5px solid ${f.gold}66`,
              opacity: clamp((t - it.a) / 0.12), transform: `scale(${0.85 + 0.15 * clamp(p)})`, boxShadow: '0 14px 40px rgba(0,0,0,0.5)'}}>
              <div style={{width: 34, height: 34, borderRadius: 17, background: `linear-gradient(145deg, ${f.goldL}, ${f.gold})`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'Montserrat', fontWeight: 900, fontSize: 19, color: '#141210', marginBottom: 8}}>{i + 1}</div>
              <div style={{fontFamily: 'Montserrat', fontWeight: 900, fontSize: fs, color: '#fff', whiteSpace: 'nowrap'}}>{it.text}</div>
              {it.sub ? <div style={{fontFamily: 'Poppins', fontWeight: 600, fontSize: Math.min(20, fs * 0.6), color: 'rgba(255,255,255,0.6)', whiteSpace: 'nowrap', marginTop: 2}}>{it.sub}</div> : null}
            </div>
          </React.Fragment>
        );
      })}
    </div>
  );
};

/* ---------- Lichtblitz an Schnitten (Yacht) ---------- */
export const Blitz: React.FC<{t: number; at: number; farbe: string}> = ({t, at, farbe}) => {
  const u = (t - at) / 0.22;
  if (u < -0.3 || u > 1) return null;
  const s = u < 0 ? clamp((u + 0.3) / 0.3) : 1 - eo(u);
  return <AbsoluteFill style={{background: `radial-gradient(80% 60% at 50% 45%, #fff, ${farbe} 55%, transparent 100%)`, opacity: 0.55 * s, mixBlendMode: 'screen'}} />;
};

/* ---------- Filmlook: Teal/Orange, Korn, wandernde Lichtflecken (Yacht) ---------- */
export const Filmlook: React.FC<{t: number; farbe: string}> = ({t, farbe}) => {
  const x = 50 + 30 * Math.sin(t * 0.35);
  const y = 30 + 20 * Math.cos(t * 0.27);
  const korn = Math.floor(t * 12) % 4;
  return (
    <>
      <AbsoluteFill style={{background: 'linear-gradient(180deg, rgba(0,70,90,0.20), rgba(0,40,60,0.05) 45%, rgba(255,140,40,0.10))', mixBlendMode: 'soft-light'}} />
      <AbsoluteFill style={{background: `radial-gradient(40% 30% at ${x}% ${y}%, ${farbe}55, transparent 70%)`, mixBlendMode: 'screen', opacity: 0.55}} />
      <AbsoluteFill style={{opacity: 0.09, mixBlendMode: 'overlay', backgroundImage: `url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='240' height='240'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' seed='${korn}'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>")`}} />
      <AbsoluteFill style={{background: 'radial-gradient(110% 80% at 50% 50%, transparent 58%, rgba(0,0,0,0.45) 100%)'}} />
    </>
  );
};

/* ---------- Durchlaufende nummerierte Liste (Finanz-Video: „1. Kein Leasing … 9. Familienbande“) ---------- */
export const Punkte: React.FC<{t: number; e: XEv; f: Farben}> = ({t, e, f}) => {
  if (t < e.a || t >= e.b) return null;
  const o = clamp((t - e.a) / 0.16) * (1 - clamp((t - (e.b - 0.16)) / 0.16));
  const items: string[] = e.items || [];
  const cur = Math.max(0, Math.min(items.length - 1, e.cur ?? 0));
  const roll = eio(clamp((t - e.a) / 0.5));
  const pos = Math.max(0, cur - 1) + roll * (cur - Math.max(0, cur - 1));
  const H = 92;
  return (
    <AbsoluteFill style={{opacity: o, background: 'linear-gradient(170deg, #f6f4ef, #e7e3da)', alignItems: 'center', justifyContent: 'center'}}>
      {e.kicker ? <div style={{position: 'absolute', top: 560, fontFamily: 'Poppins', fontWeight: 600, fontSize: 26, letterSpacing: 8, color: '#8c6531', textTransform: 'uppercase'}}>{e.kicker}</div> : null}
      <div style={{position: 'relative', width: 760, height: H * 5, overflow: 'hidden', marginTop: -160, maskImage: 'linear-gradient(transparent, #000 22%, #000 78%, transparent)'}}>
        {items.map((it, i) => {
          const y = (i - pos) * H + H * 2;
          const an = i === cur;
          return (
            <div key={i} style={{position: 'absolute', left: 0, right: 0, top: y, height: H, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
              <div style={{display: 'flex', alignItems: 'baseline', gap: 18, padding: an ? '12px 40px' : '0 40px', borderRadius: 18, background: an ? '#fff' : 'transparent',
                boxShadow: an ? '0 10px 30px rgba(0,0,0,0.12)' : 'none', border: an ? `1.5px solid ${f.gold}66` : 'none', transform: `scale(${an ? 1 + 0.06 * roll : 0.92})`}}>
                <span style={{fontFamily: 'Playfair', fontStyle: 'italic', fontWeight: 700, fontSize: 52, color: an ? '#8c6531' : '#9a948a'}}>{i + 1}.</span>
                <span style={{fontFamily: 'Playfair', fontStyle: 'italic', fontWeight: 700, fontSize: 52, color: an ? '#15130f' : '#9a948a', whiteSpace: 'nowrap'}}>{it}</span>
              </div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

/* ---------- Schwebende Begriffs-Wolke (Finanz-Video: „All-World · MSCI World · S&P 500“) ---------- */
export const Wolke: React.FC<{t: number; e: XEv; f: Farben}> = ({t, e, f}) => {
  if (t < e.a || t >= e.b) return null;
  const items: any[] = e.items || [];
  const PL = [[540, 300], [300, 400], [780, 410], [420, 520], [680, 530]];
  return (
    <div style={{position: 'absolute', left: 0, top: 0, width: 1080, height: 1920}}>
      {items.slice(0, 5).map((it, i) => {
        const at = it.a ?? e.a + i * 0.25;
        const p = eo((t - at) / 0.4);
        const o = clamp((t - at) / 0.2) * (1 - clamp((t - (e.b - 0.25)) / 0.25));
        const [x, y] = PL[i];
        const dx = 10 * Math.sin((t - e.a) * 1.3 + i), dy = 8 * Math.cos((t - e.a) * 1.1 + i * 2);
        return (
          <div key={i} style={{position: 'absolute', left: x + dx, top: y + dy, transform: `translate(-50%, -50%) scale(${0.7 + 0.3 * p})`, opacity: o, filter: `blur(${(1 - p) * 8}px)`,
            padding: '14px 28px', borderRadius: 40, background: i === 0 ? `linear-gradient(120deg, ${f.gold}, ${f.goldL})` : 'rgba(255,255,255,0.92)',
            fontFamily: 'Montserrat', fontWeight: 900, fontSize: 34, color: '#141210', whiteSpace: 'nowrap', boxShadow: '0 12px 30px rgba(0,0,0,0.35)'}}>{it.text}</div>
        );
      })}
    </div>
  );
};
