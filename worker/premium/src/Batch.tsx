import React from 'react';
import {AbsoluteFill, Audio, Img, OffthreadVideo, Sequence, staticFile, useCurrentFrame} from 'remotion';
import {Blitz, Durch, Filmlook, Flow, Funken, Kasten, Klick, Mass, Ort, Profil, Punkte, Ring, Tage, Ticket, Titel3D, Wolke} from './Extra';

const FPS = 30;
let GOLD = '#D4AB72';
let GOLD_L = '#F0D3A4';
const INK = 'rgba(18,18,20,0.86)';
const OUTRO = 2.6;

export type Ev = {type: string; a: number; b: number; [k: string]: any};
export type BProps = {
  file: string;
  dur: number;
  base: {s: number; tx: number; ty: number; ox: number; oy: number};
  cam: [number, number, number][];
  sweeps: number[];
  chunks: [string, number, boolean?][][];
  events: Ev[];
  outro: {line: string; gold: string; handle?: string; site?: string} | null;
  theme?: {accent: string; accentL: string};
  brand?: {novus?: boolean; logo?: string};
  sfx: {t: number; k: string; vol: number}[];
  look?: string;
  blitz?: number[];
};
export const totalFrames = (p: BProps) => Math.round((p.dur + (p.outro ? OUTRO : 0)) * FPS);

const clamp = (x: number, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const eo = (x: number) => 1 - Math.pow(1 - clamp(x), 3);
const eio = (x: number) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
const back = (x: number) => {
  const c = clamp(x);
  const k = 1.9;
  return 1 + (k + 1) * Math.pow(c - 1, 3) + k * Math.pow(c - 1, 2);
};
const io = (t: number, a: number, b: number, dIn = 0.42, dOut = 0.22) => {
  const p = back((t - a) / dIn);
  const o = clamp((t - a) / 0.14) * (1 - clamp((t - (b - dOut)) / dOut));
  const ex = eio(clamp((t - (b - dOut)) / dOut));
  return {p, o, ex, on: t >= a && t < b};
};
const Pop: React.FC<{t: number; a: number; b: number; children: React.ReactNode; style?: React.CSSProperties; dy?: number; rot?: number}> = ({t, a, b, children, style, dy = 34, rot = 0}) => {
  const {p, o, ex, on} = io(t, a, b);
  if (!on) return null;
  return <div style={{opacity: o, transform: `translateY(${(1 - p) * dy - ex * 16}px) scale(${0.86 + 0.14 * p}) rotate(${(1 - p) * rot}deg)`, ...style}}>{children}</div>;
};

const card: React.CSSProperties = {
  background: INK,
  border: '1.5px solid rgba(212,171,114,0.42)',
  borderRadius: 26,
  boxShadow: '0 14px 40px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.08)',
  display: 'flex',
  alignItems: 'center',
};
const label: React.CSSProperties = {fontFamily: 'Montserrat', fontWeight: 700, color: '#fff', lineHeight: 1.05, whiteSpace: 'nowrap'};
const small: React.CSSProperties = {fontFamily: 'Poppins', fontWeight: 600, color: GOLD, letterSpacing: 3, fontSize: 19, textTransform: 'uppercase', lineHeight: 1, whiteSpace: 'nowrap'};
const STAGE: React.CSSProperties = {position: 'absolute', left: 90, top: 318, width: 900, height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center'};

const IconBox: React.FC<{size?: number; children: React.ReactNode; dim?: boolean}> = ({size = 72, children, dim}) => (
  <div style={{width: size, height: size, borderRadius: size * 0.3, background: dim ? '#2c2c30' : `linear-gradient(145deg, ${GOLD_L}, ${GOLD} 55%, #A9824C)`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, boxShadow: dim ? 'none' : '0 6px 18px rgba(212,171,114,0.35)'}}>{children}</div>
);
const Svg: React.FC<{s?: number; children: React.ReactNode; stroke?: string}> = ({s = 40, children, stroke = '#17140f'}) => (
  <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke={stroke} strokeWidth={2.1} strokeLinecap="round" strokeLinejoin="round">
    {children}
  </svg>
);
const ICONS: Record<string, React.ReactNode> = {
  fork: (
    <>
      <path d="M7 3v7a2 2 0 0 0 2 2v9" />
      <path d="M5 3v5M9 3v5M11 3v7a2 2 0 0 1-2 2" />
      <path d="M17 21V3c-2 1.5-3 4-3 7 0 1.5 1 3 3 3" />
    </>
  ),
  build: (
    <>
      <path d="M4 21V5a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1v16" />
      <path d="M14 10h5a1 1 0 0 1 1 1v10M3 21h18" />
      <path d="M7.5 8h3M7.5 12h3M7.5 16h3M17 14h.5M17 17.5h.5" />
    </>
  ),
  pin: (
    <>
      <path d="M12 21s7-6.2 7-11.5A7 7 0 0 0 5 9.5C5 14.8 12 21 12 21z" />
      <circle cx="12" cy="9.5" r="2.4" />
    </>
  ),
  send: (
    <>
      <path d="M21 3 10.5 13.5" />
      <path d="M21 3l-6.5 18-4-7.5L3 9.5 21 3z" />
    </>
  ),
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  x: <path d="M6 6l12 12M18 6L6 18" />,
  user: (
    <>
      <circle cx="12" cy="8.5" r="3.6" />
      <path d="M4.5 20.5c1-4 4-5.5 7.5-5.5s6.5 1.5 7.5 5.5" />
    </>
  ),
  chat: (
    <>
      <path d="M20 12a8 8 0 0 1-11.8 7L4 20l1-4.2A8 8 0 1 1 20 12z" />
      <path d="M8.5 11h7M8.5 14h4.5" />
    </>
  ),
  phone: <path d="M6 3h3l1.5 5-2 1.5a12 12 0 0 0 6 6l1.5-2 5 1.5v3a2 2 0 0 1-2 2A16 16 0 0 1 4 5a2 2 0 0 1 2-2z" />,
  doc: (
    <>
      <path d="M7 3h7l4 4v14H7z" />
      <path d="M14 3v4h4M10 12h5M10 16h5" />
    </>
  ),
  euro: <path d="M17 6.5A6.5 6.500 0 1 0 17 17.500M4 10.500h9M4 13.500h9" />,
  cam: (
    <>
      <path d="M4 8h3l1.500-2h7L17 8h3v11H4z" />
      <circle cx="12" cy="13.500" r="3.300" />
    </>
  ),
  laptop: (
    <>
      <path d="M5 5h14v10H5z" />
      <path d="M2.500 19h19" />
    </>
  ),
  mobile: (
    <>
      <path d="M8 3h8a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" />
      <path d="M11 18h2" />
    </>
  ),
  bot: (
    <>
      <path d="M5 9h14v10H5zM12 9V5" />
      <circle cx="12" cy="4" r="1" />
      <path d="M9 13.500h.500M14.500 13.500h.500M9.500 16.500h5" />
    </>
  ),
  insta: (
    <>
      <path d="M7 3h10a4 4 0 0 1 4 4v10a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4V7a4 4 0 0 1 4-4z" />
      <circle cx="12" cy="12" r="4" />
      <path d="M17.200 6.800h.100" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="6.500" />
      <path d="M16 16l4.500 4.500" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8.500" />
      <path d="M12 7.500V12l3 2" />
    </>
  ),
  up: <path d="M4 17l5-5 4 3 7-8M15 7h5v5" />,
  star: <path d="M12 3.500l2.600 5.400 5.900.800-4.300 4.100 1 5.900L12 16.900 6.800 19.700l1-5.900L3.500 9.700l5.900-.800z" />,
  hand: <path d="M7 12l3.500 3.500L14 12l3 3 4-4M3 11l4-4 4 3 3-2 4 3" />,
  bulb: <path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.500 10.900V16h7v-2.100A6 6 0 0 0 12 3z" />,
};

const Bars: React.FC<{h: number; p?: number}> = ({h, p = 1}) => {
  const g = (i: number) => eo((p - i * 0.18) / 0.55);
  const pts = ['17,82 31,82 39,48 25,48', '39,82 53,82 61,28 47,28', '61,82 75,82 83,8 69,8'];
  return (
    <svg width={(h * 70) / 78} height={h} viewBox="15 6 70 78">
      {pts.map((pt, i) => (
        <polygon key={i} points={pt} fill={i === 2 ? GOLD : '#fff'} style={{transformOrigin: '50px 82px', transform: `scaleY(${g(i)})`, opacity: g(i)}} />
      ))}
    </svg>
  );
};
const Lockup: React.FC<{h: number; p?: number}> = ({h, p = 1}) => {
  const tx = eo((p - 0.35) / 0.6);
  return (
    <div style={{display: 'flex', alignItems: 'center', gap: h * 0.28}}>
      <Bars h={h} p={p} />
      <div style={{overflow: 'hidden', paddingRight: 6}}>
        <div style={{transform: `translateX(${(1 - tx) * -40}px)`, opacity: tx}}>
          <div style={{fontFamily: 'Montserrat', fontWeight: 900, fontSize: h * 0.62, letterSpacing: h * 0.035, color: '#fff', lineHeight: 1}}>NOVUS</div>
          <div style={{fontFamily: 'Poppins', fontWeight: 600, fontSize: h * 0.2, letterSpacing: h * 0.085, color: GOLD, marginTop: h * 0.1, lineHeight: 1}}>MEDIA</div>
        </div>
      </div>
    </div>
  );
};

/* ---------- Bausteine der Bühne ---------- */
const rich = (s: string, goldStyle: React.CSSProperties) =>
  s.split('*').map((part, i) => (i % 2 ? <span key={i} style={goldStyle}>{part}</span> : <React.Fragment key={i}>{part}</React.Fragment>));

const Title: React.FC<{t: number; e: Ev}> = ({t, e}) => {
  const {o, on} = io(t, e.a, e.b, 0.4, 0.25);
  if (!on) return null;
  const k = eo((t - e.a) / 0.35);
  const h = eo((t - e.a - 0.12) / 0.45);
  const line = eo((t - e.a - 0.35) / 0.5);
  return (
    <div style={{...STAGE, flexDirection: 'column', opacity: o}}>
      <div style={{...small, fontSize: 22, letterSpacing: 6, opacity: k, transform: `translateY(${(1 - k) * 14}px)`}}>{e.kicker}</div>
      <div style={{fontFamily: 'Montserrat', fontWeight: 900, fontSize: e.size || 70, color: '#fff', lineHeight: 1.05, marginTop: 16, whiteSpace: 'nowrap', opacity: h, transform: `translateY(${(1 - h) * 30}px) scale(${0.94 + 0.06 * h})`, textShadow: '0 6px 30px rgba(0,0,0,0.8)'}}>
        {rich(e.text, {fontFamily: 'Playfair', fontStyle: 'italic', fontWeight: 700, color: GOLD_L, fontSize: (e.size || 70) * 1.12})}
      </div>
      <div style={{height: 5, borderRadius: 3, marginTop: 18, width: 520 * line, background: `linear-gradient(90deg, transparent, ${GOLD} 20%, ${GOLD_L} 80%, transparent)`}} />
    </div>
  );
};

const ChipBody: React.FC<{it: any; t: number}> = ({it, t}) => {
  const st = it.state as string | undefined; // 'ok' | 'no'
  const sa = it.sa ?? it.a + 0.35;
  const sp = st ? back((t - sa) / 0.3) : 0;
  const no = st === 'no' && t >= sa;
  return (
    <div style={{...card, padding: '16px 30px 16px 16px', gap: 18, position: 'relative', borderColor: st === 'ok' && t >= sa ? GOLD : 'rgba(212,171,114,0.42)'}}>
      <IconBox dim={no}>
        <Svg stroke={no ? 'rgba(255,255,255,0.5)' : '#17140f'}>{ICONS[it.icon] || ICONS.star}</Svg>
      </IconBox>
      <div>
        {it.sub ? <div style={{...small, marginBottom: 8, fontSize: 17, color: no ? 'rgba(255,255,255,0.4)' : GOLD}}>{it.sub}</div> : null}
        <div style={{...label, fontSize: it.size || 38, color: no ? 'rgba(255,255,255,0.45)' : '#fff', textDecoration: no ? 'line-through' : 'none', textDecorationColor: 'rgba(255,255,255,0.5)'}}>{it.text}</div>
      </div>
      {st && sp > 0 ? (
        <div style={{position: 'absolute', right: -16, top: -16, width: 44, height: 44, borderRadius: 22, background: st === 'ok' ? GOLD : '#3a3a3f', border: '3px solid #121214', display: 'flex', alignItems: 'center', justifyContent: 'center', transform: `scale(${sp})`}}>
          <Svg s={24} stroke={st === 'ok' ? '#17140f' : '#fff'}>{st === 'ok' ? ICONS.check : ICONS.x}</Svg>
        </div>
      ) : null}
    </div>
  );
};
const Chips: React.FC<{t: number; e: Ev}> = ({t, e}) => (
  <div style={{...STAGE, gap: 26}}>
    {e.items.map((it: any, i: number) => (
      <Pop key={i} t={t} a={it.a} b={e.b} rot={i % 2 ? 4 : -4}>
        <ChipBody it={it} t={t} />
      </Pop>
    ))}
  </div>
);

const fmt = (n: number) => Math.round(n).toLocaleString('de-DE');
const Counter: React.FC<{t: number; e: Ev}> = ({t, e}) => {
  const d = e.dur || 0.6;
  const u = eo((t - e.a - 0.05) / d);
  const done = t > e.a + d;
  const bump = 1 + 0.08 * Math.max(0, 1 - Math.abs(t - (e.a + d + 0.05)) / 0.14);
  const val = (to: number) => fmt((e.from || 0) + (to - (e.from || 0)) * u);
  return (
    <div style={{...STAGE, gap: 26}}>
      <Pop t={t} a={e.a} b={e.b}>
        <div style={{...card, padding: '16px 38px', gap: 22}}>
          <div style={{fontFamily: 'Montserrat', fontWeight: 900, fontSize: e.size || 96, lineHeight: 1, color: done ? GOLD : '#fff', transform: `scale(${bump})`, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap', textShadow: done ? '0 0 34px rgba(212,171,114,0.55)' : 'none'}}>
            {e.prefix || ''}
            {val(e.to)}
            {e.to2 ? ` – ${val(e.to2)}` : ''}
            {e.suffix || ''}
          </div>
          {e.label ? (
            <div>
              <div style={{...label, fontSize: 36}}>{e.label}</div>
              {e.sub ? <div style={{...small, marginTop: 9}}>{e.sub}</div> : null}
            </div>
          ) : null}
        </div>
      </Pop>
      {e.chip ? (
        <Pop t={t} a={e.chip.a} b={e.b} rot={4}>
          <ChipBody it={e.chip} t={t} />
        </Pop>
      ) : null}
    </div>
  );
};

const Steps: React.FC<{t: number; e: Ev}> = ({t, e}) => {
  const {o, on, p} = io(t, e.a, e.b);
  if (!on) return null;
  const items: {a: number; text: string}[] = e.items;
  const W = 760;
  const n = items.length;
  const xs = items.map((_, i) => (n === 1 ? W / 2 : 20 + (i * (W - 40)) / (n - 1)));
  let act = -1;
  items.forEach((it, i) => {
    if (t >= it.a) act = i;
  });
  let prog = 0;
  if (act >= 0) {
    const from = act === 0 ? 0 : xs[act - 1];
    prog = from + (xs[act] - from) * eo((t - items[act].a) / 0.3);
  }
  const line = eo((t - e.a - 0.15) / 0.7);
  return (
    <div style={{...STAGE, opacity: o}}>
      <div style={{...card, padding: '22px 44px 24px', flexDirection: 'column', alignItems: 'stretch', width: W + 88, transform: `translateY(${(1 - p) * 30}px)`}}>
        <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
          <div style={{...small, fontSize: 20}}>{e.header}</div>
          <div style={{...small, fontSize: 18, color: 'rgba(255,255,255,0.45)', fontVariantNumeric: 'tabular-nums'}}>
            {Math.max(0, act + 1)} / {n}
          </div>
        </div>
        <div style={{position: 'relative', height: 44, marginTop: 14}}>
          <div style={{position: 'absolute', left: 0, top: 19, height: 6, borderRadius: 3, width: W * line, background: 'rgba(255,255,255,0.14)'}} />
          <div style={{position: 'absolute', left: 0, top: 19, height: 6, borderRadius: 3, width: prog, background: `linear-gradient(90deg, #A9824C, ${GOLD_L})`, boxShadow: '0 0 14px rgba(212,171,114,0.6)'}} />
          {items.map((it, i) => {
            const k = back((t - it.a) / 0.36);
            const onn = t >= it.a;
            const ring = clamp((t - it.a) / 0.5);
            const show = eo((t - e.a - 0.25 - i * 0.07) / 0.3);
            return (
              <React.Fragment key={i}>
                {onn && ring < 1 ? <div style={{position: 'absolute', left: xs[i] - 16 - 26 * ring, top: 6 - 26 * ring, width: 32 + 52 * ring, height: 32 + 52 * ring, borderRadius: 80, border: `2px solid ${GOLD_L}`, opacity: 1 - ring}} /> : null}
                <div style={{position: 'absolute', left: xs[i] - 16, top: 6, width: 32, height: 32, borderRadius: 16, background: onn ? GOLD : '#2a2a2e', border: `3px solid ${onn ? GOLD_L : 'rgba(255,255,255,0.28)'}`, transform: `scale(${show * (onn ? 0.75 + 0.25 * k : 0.7)})`, display: 'flex', alignItems: 'center', justifyContent: 'center', boxSizing: 'border-box'}}>
                  {onn ? (
                    <Svg s={18} stroke="#17140f">
                      {ICONS.check}
                    </Svg>
                  ) : null}
                </div>
              </React.Fragment>
            );
          })}
        </div>
        <div style={{position: 'relative', height: 46, marginTop: 10}}>
          {act < 0 ? <div style={{...label, fontSize: 34, color: 'rgba(255,255,255,0.6)', textAlign: 'center', opacity: line}}>{e.idle}</div> : null}
          {items.map((it, i) => {
            if (i !== act) return null;
            const k = eo((t - it.a) / 0.26);
            return (
              <div key={i} style={{...label, position: 'absolute', left: 0, right: 0, textAlign: 'center', fontSize: 38, opacity: k, transform: `translateY(${(1 - k) * 18}px)`}}>
                {it.text}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

const Big: React.FC<{t: number; e: Ev}> = ({t, e}) => {
  if (t < e.a || t >= e.b) return null;
  const p = eo((t - e.a) / 0.3);
  const ex = clamp((t - (e.b - 0.18)) / 0.18);
  const line = eo((t - e.a - 0.12) / 0.38);
  return (
    <div style={{...STAGE, flexDirection: 'column', opacity: clamp((t - e.a) / 0.08) * (1 - ex)}}>
      {e.kicker ? <div style={{...small, fontSize: 22, letterSpacing: 6, marginBottom: 6, opacity: p}}>{e.kicker}</div> : null}
      <div style={{fontFamily: 'Playfair', fontStyle: 'italic', fontWeight: 700, fontSize: e.size || 118, color: GOLD_L, letterSpacing: -1, lineHeight: 1, whiteSpace: 'nowrap', transform: `translateX(${(1 - p) * -120}px) skewX(${(1 - p) * -14}deg) scale(${1 + ex * 0.06})`, textShadow: '0 6px 30px rgba(0,0,0,0.8), 0 0 46px rgba(212,171,114,0.45)', filter: `blur(${(1 - p) * 8}px)`}}>{e.text}</div>
      <div style={{height: 7, borderRadius: 4, marginTop: 10, width: (e.lw || 600) * line, background: `linear-gradient(90deg, transparent, ${GOLD} 20%, ${GOLD_L} 80%, transparent)`, alignSelf: 'center'}} />
    </div>
  );
};

const Stack: React.FC<{t: number; e: Ev}> = ({t, e}) => (
  <div style={STAGE}>
    {[0, 1, 2].map((i) => {
      const a = e.a + i * 0.26;
      const {p, o, on, ex} = io(t, a, e.b, 0.36, 0.2);
      if (!on) return null;
      const sent = t > a + 0.3;
      return (
        <div key={i} style={{position: 'absolute', left: 120 + i * 78, top: 6 + i * 62, opacity: o, transform: `translateX(${(1 - p) * -90 + ex * 120}px) scale(${0.9 + 0.1 * p})`}}>
          <div style={{...card, padding: '10px 22px 10px 10px', gap: 14, borderRadius: 20, width: 520}}>
            <IconBox size={46}>
              <Svg s={26}>{ICONS[e.icon || 'send']}</Svg>
            </IconBox>
            <div style={{...label, fontSize: 27, flex: 1}}>{e.text}</div>
            <div style={{opacity: sent ? 1 : 0.15, transform: `scale(${sent ? 1 : 0.6})`}}>
              <Svg s={30} stroke={GOLD_L}>
                {ICONS.check}
              </Svg>
            </div>
          </div>
        </div>
      );
    })}
  </div>
);

const Checks: React.FC<{t: number; e: Ev}> = ({t, e}) => {
  const {o, on} = io(t, e.a, e.b);
  if (!on) return null;
  const items: any[] = e.items;
  return (
    <div style={{...STAGE, opacity: o, flexDirection: 'column', gap: 10}}>
      {items.map((it, i) => {
        const p = back((t - it.a) / 0.36);
        if (t < it.a) return <div key={i} style={{height: 58}} />;
        const no = it.ok === false;
        return (
          <div key={i} style={{...card, padding: '8px 26px 8px 10px', gap: 14, borderRadius: 18, height: 58, boxSizing: 'border-box', minWidth: 470, opacity: clamp(p), transform: `translateX(${(1 - p) * (i % 2 ? 60 : -60)}px)`}}>
            <div style={{width: 40, height: 40, borderRadius: 20, background: no ? '#3a3a3f' : GOLD, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
              <Svg s={24} stroke={no ? '#fff' : '#17140f'}>{no ? ICONS.x : ICONS.check}</Svg>
            </div>
            <div style={{...label, fontSize: 30}}>{it.text}</div>
          </div>
        );
      })}
    </div>
  );
};

const Funnel: React.FC<{t: number; e: Ev}> = ({t, e}) => {
  const {o, on} = io(t, e.a, e.b);
  if (!on) return null;
  const rows: any[] = e.rows;
  return (
    <div style={{...STAGE, opacity: o, flexDirection: 'column', gap: 9}}>
      {rows.map((r, i) => {
        if (t < r.a) return <div key={i} style={{height: 58}} />;
        const p = eo((t - r.a) / 0.5);
        const pp = back((t - r.a) / 0.36);
        return (
          <div key={i} style={{position: 'relative', height: 58, width: 760, opacity: clamp(pp), transform: `translateY(${(1 - pp) * 16}px)`}}>
            <div style={{position: 'absolute', left: (760 - 760 * r.w) / 2, width: 760 * r.w * (0.25 + 0.75 * p), marginLeft: (760 * r.w * 0.75 * (1 - p)) / 2, top: 0, bottom: 0, borderRadius: 16, background: i === rows.length - 1 ? `linear-gradient(145deg, ${GOLD_L}, ${GOLD})` : INK, border: '1.5px solid rgba(212,171,114,0.5)', boxSizing: 'border-box'}} />
            <div style={{position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 14}}>
              <span style={{fontFamily: 'Montserrat', fontWeight: 900, fontSize: 36, color: i === rows.length - 1 ? '#17140f' : GOLD_L, whiteSpace: 'nowrap'}}>{r.value}</span>
              <span style={{...label, fontSize: 26, color: i === rows.length - 1 ? '#17140f' : '#fff'}}>{r.label}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
};

const Num: React.FC<{t: number; e: Ev}> = ({t, e}) => (
  <div style={STAGE}>
    <Pop t={t} a={e.a} b={e.b} rot={-3}>
      <div style={{...card, padding: '14px 40px 14px 18px', gap: 24}}>
        <div style={{width: 112, height: 112, borderRadius: 30, background: `linear-gradient(145deg, ${GOLD_L}, ${GOLD} 55%, #A9824C)`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'Montserrat', fontWeight: 900, fontSize: 78, color: '#17140f', boxShadow: '0 6px 22px rgba(212,171,114,0.4)'}}>{e.n}</div>
        <div>
          <div style={{...small, marginBottom: 10, fontSize: 20}}>{e.kicker}</div>
          <div style={{...label, fontSize: e.size || 44, fontWeight: 900}}>{e.text}</div>
          {e.sub ? <div style={{...label, fontSize: 26, marginTop: 8, color: 'rgba(255,255,255,0.6)', opacity: eo((t - (e.sa ?? e.a + 0.6)) / 0.3)}}>{e.sub}</div> : null}
        </div>
      </div>
    </Pop>
  </div>
);

const Follow: React.FC<{t: number; e: Ev}> = ({t, e}) => {
  const {o, on, p} = io(t, e.a, e.b);
  if (!on) return null;
  const click = e.click;
  const done = t >= click;
  const cur = eio(clamp((t - (click - 0.5)) / 0.44));
  const press = 1 - 0.08 * Math.max(0, 1 - Math.abs(t - click) / 0.09);
  const ring = clamp((t - click) / 0.45);
  return (
    <div style={{...STAGE, opacity: o}}>
      <div style={{...card, padding: '18px 22px 18px 20px', gap: 20, transform: `translateY(${(1 - p) * 30}px)`}}>
        <div style={{width: 92, height: 92, borderRadius: 46, background: '#0f0f11', border: `3px solid ${GOLD}`, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
          <Bars h={48} />
        </div>
        <div style={{marginRight: 22}}>
          <div style={{...label, fontSize: 40}}>{e.handle ?? '_novusmedia'}</div>
          <div style={{...small, marginTop: 9, fontSize: 17, color: 'rgba(255,255,255,0.55)'}}>{e.name ?? 'Novus Media'}</div>
        </div>
        <div style={{position: 'relative'}}>
          {done && ring < 1 ? <div style={{position: 'absolute', inset: -30 * ring, borderRadius: 40, border: `2px solid ${GOLD_L}`, opacity: 1 - ring}} /> : null}
          <div style={{padding: '18px 34px', borderRadius: 18, background: done ? 'transparent' : `linear-gradient(145deg, ${GOLD_L}, ${GOLD})`, border: `2.5px solid ${GOLD}`, fontFamily: 'Montserrat', fontWeight: 900, fontSize: 30, color: done ? GOLD_L : '#17140f', transform: `scale(${press})`, display: 'flex', alignItems: 'center', gap: 10, width: 178, justifyContent: 'center', boxSizing: 'border-box', height: 74}}>
            {done ? (
              <>
                Gefolgt
                <Svg s={26} stroke={GOLD_L}>
                  {ICONS.check}
                </Svg>
              </>
            ) : (
              'Folgen'
            )}
          </div>
          <svg width={54} height={54} viewBox="0 0 24 24" style={{position: 'absolute', left: 118 + (1 - cur) * 130, top: 46 + (1 - cur) * 100, opacity: clamp((t - (click - 0.52)) / 0.1) * (1 - clamp((t - (click + 0.5)) / 0.2)), filter: 'drop-shadow(0 4px 6px rgba(0,0,0,0.7))', transform: `scale(${press})`}}>
            <path d="M5 3l14 8-6.2 1.6L10 19 5 3z" fill="#fff" stroke="#111" strokeWidth={1.3} strokeLinejoin="round" />
          </svg>
        </div>
      </div>
    </div>
  );
};

const Contact: React.FC<{t: number; e: Ev}> = ({t, e}) => {
  const {o, on, p} = io(t, e.a, e.b);
  if (!on) return null;
  const l2 = eo((t - e.a - 0.6) / 0.4);
  const typing = (i: number) => 0.35 + 0.65 * Math.max(0, Math.sin((t - e.a) * 7 - i * 0.9));
  return (
    <div style={{...STAGE, opacity: o}}>
      <div style={{...card, padding: '20px 36px 20px 22px', gap: 24, transform: `translateY(${(1 - p) * 30}px) scale(${0.9 + 0.1 * p})`}}>
        <IconBox size={96}>
          <Svg s={56}>{ICONS.chat}</Svg>
        </IconBox>
        <div>
          <div style={{display: 'flex', alignItems: 'center', gap: 16}}>
            <div style={{...label, fontSize: 44, fontWeight: 900}}>{e.text || 'Schreib uns'}</div>
            <div style={{display: 'flex', gap: 6}}>
              {[0, 1, 2].map((i) => (
                <div key={i} style={{width: 10, height: 10, borderRadius: 5, background: GOLD, opacity: typing(i)}} />
              ))}
            </div>
          </div>
          <div style={{display: 'flex', gap: 14, marginTop: 14, opacity: l2, transform: `translateY(${(1 - l2) * 10}px)`}}>
            <div style={{fontFamily: 'Poppins', fontWeight: 600, fontSize: 25, color: GOLD_L}}>{e.line1 ?? '@_novusmedia'}</div>
            {(e.line2 ?? 'novus-media.de') ? <div style={{fontFamily: 'Poppins', fontWeight: 600, fontSize: 25, color: 'rgba(255,255,255,0.35)'}}>·</div> : null}
            <div style={{fontFamily: 'Poppins', fontWeight: 600, fontSize: 25, color: '#fff'}}>{e.line2 ?? 'novus-media.de'}</div>
          </div>
        </div>
      </div>
    </div>
  );
};


/* ---------- nachgebaute Oberflächen (Bühne) ---------- */
const Chat: React.FC<{t: number; e: Ev}> = ({t, e}) => {
  const {o, on, p} = io(t, e.a, e.b);
  if (!on) return null;
  const lines: string[] = e.lines;
  return (
    <div style={{...STAGE, opacity: o}}>
      <div style={{display: 'flex', flexDirection: 'column', gap: 10, alignItems: 'flex-end', width: 620, transform: `translateY(${(1 - p) * 30}px)`}}>
        {lines.map((ln, i) => {
          const k = back((t - e.a - 0.15 - i * 0.45) / 0.34);
          if (k <= 0) return <div key={i} style={{height: 62}} />;
          return (
            <div key={i} style={{padding: '14px 26px', borderRadius: '26px 26px 6px 26px', background: `linear-gradient(145deg, ${GOLD_L}, ${GOLD})`, fontFamily: 'Montserrat', fontWeight: 700, fontSize: 30, color: '#17140f', whiteSpace: 'nowrap', transform: `scale(${k})`, transformOrigin: '100% 100%', boxShadow: '0 8px 22px rgba(0,0,0,0.5)'}}>
              {ln}
            </div>
          );
        })}
        <div style={{...small, fontSize: 16, color: 'rgba(255,255,255,0.5)', opacity: clamp((t - e.a - 0.3 - lines.length * 0.45) / 0.2)}}>Gesendet ✓</div>
      </div>
    </div>
  );
};

const SearchBar: React.FC<{t: number; e: Ev}> = ({t, e}) => {
  const {o, on, p} = io(t, e.a, e.b);
  if (!on) return null;
  const n = Math.floor(clamp((t - e.a - 0.25) / (e.type_dur || 0.9)) * e.text.length);
  const blink = Math.floor(t * 3) % 2 === 0;
  const no = e.cross != null && t >= e.cross;
  const kx = back((t - (e.cross ?? 1e9)) / 0.3);
  return (
    <div style={{...STAGE, opacity: o}}>
      <div style={{position: 'relative', transform: `translateY(${(1 - p) * 30}px) scale(${0.92 + 0.08 * p})`}}>
        <div style={{display: 'flex', alignItems: 'center', gap: 18, width: 700, height: 96, borderRadius: 48, background: '#f4f2ee', padding: '0 34px', boxSizing: 'border-box', boxShadow: '0 16px 44px rgba(0,0,0,0.6)', opacity: no ? 0.55 : 1}}>
          <Svg s={40} stroke="#6b6b6b">{ICONS.search}</Svg>
          <div style={{fontFamily: 'Poppins', fontWeight: 600, fontSize: 36, color: '#1d1d1f', whiteSpace: 'nowrap', textDecoration: no ? 'line-through' : 'none'}}>
            {e.text.slice(0, n)}
            <span style={{opacity: blink && !no ? 1 : 0, color: '#1d1d1f'}}>|</span>
          </div>
        </div>
        {no ? (
          <div style={{position: 'absolute', right: -18, top: -18, width: 56, height: 56, borderRadius: 28, background: '#3a3a3f', border: '3px solid #121214', display: 'flex', alignItems: 'center', justifyContent: 'center', transform: `scale(${kx})`}}>
            <Svg s={30} stroke="#fff">{ICONS.x}</Svg>
          </div>
        ) : null}
      </div>
    </div>
  );
};

const Call: React.FC<{t: number; e: Ev}> = ({t, e}) => {
  const {o, on, p} = io(t, e.a, e.b);
  if (!on) return null;
  const ring = 1 + 0.06 * Math.sin((t - e.a) * 22) * (t - e.a < 1.4 ? 1 : 0);
  return (
    <div style={{...STAGE, opacity: o}}>
      <div style={{display: 'flex', alignItems: 'center', gap: 22, padding: '16px 20px 16px 26px', borderRadius: 60, background: 'rgba(30,30,34,0.94)', border: '1.5px solid rgba(255,255,255,0.14)', boxShadow: '0 16px 44px rgba(0,0,0,0.6)', transform: `translateY(${(1 - p) * -60}px)`}}>
        <div style={{marginRight: 30}}>
          <div style={{...label, fontSize: 36}}>{e.name}</div>
          <div style={{fontFamily: 'Poppins', fontWeight: 600, fontSize: 20, color: 'rgba(255,255,255,0.55)', marginTop: 6}}>{e.sub || 'Eingehender Anruf'}</div>
        </div>
        <div style={{width: 76, height: 76, borderRadius: 38, background: '#e5484d', display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
          <div style={{transform: 'rotate(135deg)', display: 'flex'}}><Svg s={38} stroke="#fff">{ICONS.phone}</Svg></div>
        </div>
        <div style={{width: 76, height: 76, borderRadius: 38, background: '#30a46c', display: 'flex', alignItems: 'center', justifyContent: 'center', transform: `scale(${ring})`}}>
          <Svg s={38} stroke="#fff">{ICONS.phone}</Svg>
        </div>
      </div>
    </div>
  );
};

/* ---------- Vollbild-Karte: kurz weg vom Sprecher, die Stimme läuft weiter ---------- */
const Full: React.FC<{t: number; e: Ev}> = ({t, e}) => {
  if (t < e.a || t >= e.b) return null;
  const pin = eo((t - e.a) / 0.22);
  const pout = clamp((t - (e.b - 0.18)) / 0.18);
  const paper = e.bg === 'paper';
  const ink = paper ? '#15130f' : '#fff';
  const k = eo((t - e.a - 0.05) / 0.3);
  const h = back((t - e.a - 0.12) / 0.42);
  const acc = eo((t - e.a - 0.3) / 0.35);
  const sub = eo((t - (e.sa ?? e.a + 0.28)) / 0.3);
  const d = e.dur || 0.7;
  const u = eo((t - e.a - 0.1) / d);
  const val = (to: number) => fmt((e.from || 0) + (to - (e.from || 0)) * u);
  const done = e.num && t > e.a + 0.1 + d;
  return (
    <AbsoluteFill style={{opacity: pin * (1 - pout), transform: `scale(${1.08 - 0.08 * pin + 0.05 * pout})`, background: paper ? 'linear-gradient(160deg, #f3efe7, #ded7ca)' : 'radial-gradient(90% 60% at 50% 46%, #2a2218 0%, #0c0b0a 62%, #060606 100%)', alignItems: 'center', justifyContent: 'center'}}>
      {!paper ? <div style={{position: 'absolute', inset: 0, backgroundImage: 'linear-gradient(rgba(212,171,114,0.07) 1px, transparent 1px), linear-gradient(90deg, rgba(212,171,114,0.07) 1px, transparent 1px)', backgroundSize: '90px 90px', maskImage: 'radial-gradient(60% 45% at 50% 46%, #000, transparent)'}} /> : null}
      <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', marginTop: -220}}>
        {e.kicker ? <div style={{...small, fontSize: 28, letterSpacing: 9, color: paper ? '#8c6531' : GOLD, opacity: k, transform: `translateY(${(1 - k) * 16}px)`}}>{e.kicker}</div> : null}
        {e.num ? (
          <div style={{fontFamily: 'Bebas', fontSize: e.size || 300, lineHeight: 0.95, color: done ? (paper ? '#8c6531' : GOLD_L) : ink, marginTop: 18, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums', transform: `scale(${0.8 + 0.2 * clamp(h)})`, textShadow: paper ? 'none' : '0 0 70px rgba(212,171,114,0.35)'}}>
            {e.num.prefix || ''}
            {val(e.num.to)}
            {e.num.to2 ? `–${val(e.num.to2)}` : ''}
            {e.num.suffix || ''}
          </div>
        ) : null}
        {e.big ? (
          <div style={{position: 'relative', marginTop: e.num ? 0 : 22}}>
            <div style={{fontFamily: 'Bebas', fontSize: e.bsize || 210, lineHeight: 0.92, color: ink, letterSpacing: 2, textAlign: 'center', whiteSpace: 'pre', transform: `scale(${0.82 + 0.18 * clamp(h)})`, opacity: clamp(h)}}>{e.big}</div>
            {e.accent ? <div style={{position: 'absolute', left: 0, right: 0, bottom: -(e.bsize || 210) * 0.34, textAlign: 'center', fontFamily: 'Playfair', fontStyle: 'italic', fontWeight: 700, fontSize: (e.bsize || 210) * 0.5, color: paper ? '#8c6531' : GOLD_L, whiteSpace: 'nowrap', opacity: acc, transform: `translateY(${(1 - acc) * 24}px) rotate(-3deg)`, textShadow: paper ? 'none' : '0 6px 24px rgba(0,0,0,0.7)'}}>{e.accent}</div> : null}
          </div>
        ) : null}
        {e.sub ? <div style={{fontFamily: 'Montserrat', fontWeight: 700, fontSize: 44, color: paper ? '#4a443a' : 'rgba(255,255,255,0.78)', marginTop: e.accent ? (e.bsize || 210) * 0.5 : 34, opacity: sub, whiteSpace: 'nowrap', transform: `translateY(${(1 - sub) * 16}px)`}}>{e.sub}</div> : null}
      </div>
      {e.num ? <div style={{position: 'absolute', left: 0, top: 0, width: 1080, height: 1920}}><Funken t={t} at={e.a + 0.1 + d} x={540} y={700} n={34} farbe={paper ? '#8c6531' : GOLD_L} weite={420} /></div> : null}
    </AbsoluteFill>
  );
};


/* ---------- B-Roll und Bilder des Kunden ---------- */
const MediaIn: React.FC<{e: Ev; style?: React.CSSProperties}> = ({e, style}) =>
  e.img ? (
    <Img src={staticFile(e.src)} style={{width: '100%', height: '100%', objectFit: 'cover', ...style}} />
  ) : (
    <Sequence from={Math.round(e.a * FPS)} layout="none">
      <OffthreadVideo muted src={staticFile(e.src)} style={{width: '100%', height: '100%', objectFit: 'cover', ...style}} />
    </Sequence>
  );
// kleine Karte unter dem Sprecher (Sprecher-als-Karte): „zeig, worüber du redest“
const Media: React.FC<{t: number; e: Ev}> = ({t, e}) => {
  const {o, on, p} = io(t, e.a, e.b);
  if (!on) return null;
  const kb = 1.04 + 0.06 * clamp((t - e.a) / Math.max(1, e.b - e.a));
  return (
    <div style={{...STAGE, opacity: o}}>
      <div style={{width: 640, height: 330, marginTop: 110, borderRadius: 30, overflow: 'hidden', border: `2px solid ${GOLD}`, boxShadow: '0 20px 50px rgba(0,0,0,0.6)', transform: `translateY(${(1 - p) * 40}px) scale(${0.9 + 0.1 * p}) rotate(${(1 - p) * -3}deg)`, background: '#111'}}>
        <div style={{width: '100%', height: '100%', transform: `scale(${e.img ? kb : 1})`}}>
          <MediaIn e={e} />
        </div>
      </div>
    </div>
  );
};
// Zwischenschnitt im Vollbild: zoomt sich weich auf die volle Größe ein
const BrollFull: React.FC<{t: number; e: Ev}> = ({t, e}) => {
  if (t < e.a || t >= e.b) return null;
  const pin = eo((t - e.a) / 0.4);
  const o = clamp((t - e.a) / 0.12) * (1 - clamp((t - (e.b - 0.12)) / 0.12));
  return (
    <AbsoluteFill style={{opacity: o, background: '#000'}}>
      <AbsoluteFill style={{transform: `scale(${1.1 - 0.1 * pin})`}}>
        <MediaIn e={e} />
      </AbsoluteFill>
      <AbsoluteFill style={{background: 'linear-gradient(180deg, transparent 55%, rgba(0,0,0,0.55) 78%, rgba(0,0,0,0.2) 100%)'}} />
    </AbsoluteFill>
  );
};
// Handy-Mockup auf ruhiger Fläche
const Phone: React.FC<{t: number; e: Ev}> = ({t, e}) => {
  if (t < e.a || t >= e.b) return null;
  const pin = back((t - e.a) / 0.45);
  const o = clamp((t - e.a) / 0.15) * (1 - clamp((t - (e.b - 0.18)) / 0.18));
  const tilt = (1 - clamp(pin)) * 14 - 3 + 2 * Math.sin((t - e.a) * 0.9);
  return (
    <AbsoluteFill style={{opacity: o, background: 'radial-gradient(90% 60% at 50% 42%, #2a2218 0%, #0c0b0a 62%, #060606 100%)', alignItems: 'center'}}>
      <div style={{marginTop: 300, width: 500, height: 1000, borderRadius: 70, padding: 14, background: 'linear-gradient(145deg, #3a3a3e, #121214)', boxShadow: `0 40px 100px rgba(0,0,0,0.8), 0 0 0 2px rgba(255,255,255,0.08), 0 0 90px rgba(212,171,114,0.18)`, transform: `translateY(${(1 - clamp(pin)) * 220}px) rotate(${tilt}deg) scale(${0.9 + 0.1 * clamp(pin)})`, boxSizing: 'border-box'}}>
        <div style={{position: 'relative', width: '100%', height: '100%', borderRadius: 58, overflow: 'hidden', background: '#000'}}>
          <MediaIn e={e} />
          <div style={{position: 'absolute', left: '50%', top: 16, width: 130, height: 34, marginLeft: -65, borderRadius: 17, background: '#000'}} />
        </div>
      </div>
    </AbsoluteFill>
  );
};

const FF = () => ({gold: GOLD, goldL: GOLD_L});
const RENDER: Record<string, React.FC<{t: number; e: Ev}>> = {titel3d: (pr) => <Titel3D {...pr} f={FF()} />, mass: (pr) => <Mass {...pr} f={FF()} />, ort: (pr) => <Ort {...pr} f={FF()} />, tage: (pr) => <Tage {...pr} f={FF()} />, kasten: (pr) => <Kasten {...pr} f={FF()} />, durch: (pr) => <Durch {...pr} f={FF()} />, ring: (pr) => <Ring {...pr} f={FF()} />, ticket: (pr) => <Ticket {...pr} f={FF()} />, klick: (pr) => <Klick {...pr} f={FF()} />, profil: (pr) => <Profil {...pr} f={FF()} />, flow: (pr) => <Flow {...pr} f={FF()} />, punkte: (pr) => <Punkte {...pr} f={FF()} />, wolke: (pr) => <Wolke {...pr} f={FF()} />, media: Media, behind: (pr) => <BehindKicker {...pr} />, chat: Chat, search: SearchBar, call: Call, title: Title, chips: Chips, counter: Counter, steps: Steps, big: Big, stack: Stack, checks: Checks, funnel: Funnel, num: Num, follow: Follow, contact: Contact};

/* ---------- Abzeichen, Untertitel, Kamera, Abspann ---------- */
const Badge: React.FC<{t: number; dur: number; hide?: boolean; brand?: BProps['brand']}> = ({t, dur, hide, brand}) => {
  if (hide || !brand || (!brand.novus && !brand.logo)) return null;
  const p = clamp((t - 0.1) / 0.9);
  const end = 1 - clamp((t - (dur - 0.05)) / 0.2);
  return (
    <div style={{position: 'absolute', left: 0, right: 0, top: 262, display: 'flex', justifyContent: 'center', transform: 'translateY(-50%)', opacity: end * 0.9}}>
      <div style={{filter: 'drop-shadow(0 6px 18px rgba(0,0,0,0.7))'}}>
        {brand.logo ? <img src={staticFile(brand.logo)} style={{height: 64, opacity: p, borderRadius: 12}} /> : <Lockup h={50} p={p} />}
      </div>
    </div>
  );
};

const Captions: React.FC<{t: number; chunks: BProps['chunks']; dur: number; y?: number; paper?: boolean}> = ({t, chunks, dur, y = 586, paper}) => {
  let idx = -1;
  for (let i = 0; i < chunks.length; i++) if (t >= chunks[i][0][1] - 0.04) idx = i;
  if (idx < 0) return null;
  const ch = chunks[idx];
  const next = chunks[idx + 1] ? chunks[idx + 1][0][1] - 0.04 : dur - 0.02;
  const last = ch[ch.length - 1][1];
  if (t > Math.min(next, last + 1.1)) return null;
  const big = ch.length === 1 && ch[0][2];
  const cin = eo((t - (ch[0][1] - 0.04)) / 0.14);
  return (
    <div style={{position: 'absolute', left: 0, right: 0, top: y, display: 'flex', justifyContent: 'center', transform: 'translateY(-50%)'}}>
      <div style={{display: 'flex', alignItems: 'baseline', gap: 16, transform: `scale(${0.94 + 0.06 * cin})`, whiteSpace: 'nowrap'}}>
        {ch.map(([w, s, em], i) => {
          const k = eo((t - (s - 0.04)) / 0.13);
          const long = w.length > 15;
          const st: React.CSSProperties = em ? {fontFamily: 'Playfair', fontStyle: 'italic', fontWeight: 700, fontSize: long ? 64 : big ? 84 : 72, color: paper ? '#8c6531' : GOLD_L, letterSpacing: -0.5} : {fontFamily: 'Montserrat', fontWeight: 700, fontSize: long ? 50 : 56, color: paper ? '#15130f' : '#fff'};
          return (
            <span key={i} style={{...st, lineHeight: 1, opacity: k, transform: `translateY(${(1 - k) * 16}px)`, display: 'inline-block', textShadow: paper ? 'none' : '0 3px 16px rgba(0,0,0,0.95), 0 1px 3px rgba(0,0,0,0.9)'}}>
              {w}
            </span>
          );
        })}
      </div>
    </div>
  );
};

// Sprecher-als-Karte: 0 = Vollbild, 1 = Karte oben, Grafik darunter
const CARD = {x: 190, y: 300, w: 700, h: 700, r: 48, s: 0.62, dy: -530};
const frameAmt = (t: number, evs: Ev[]) => {
  let f = 0;
  for (const e of evs) if (e.frame) f = Math.max(f, eio(clamp((t - (e.a - 0.3)) / 0.36)) * (1 - eio(clamp((t - (e.b - 0.12)) / 0.36))));
  return f;
};
const fullOn = (t: number, evs: Ev[]) => evs.find((e) => (e.type === 'full' || e.type === 'broll' || e.type === 'phone' || e.type === 'punkte') && t >= e.a + 0.06 && t < e.b - 0.1);
const behindOn = (t: number, evs: Ev[]) => evs.some((e) => e.type === 'behind' && t >= e.a - 0.05 && t < e.b);
const BehindText: React.FC<{t: number; e: Ev}> = ({t, e}) => {
  if (t < e.a || t >= e.b) return null;
  const pin = eo((t - e.a) / 0.32);
  const pout = clamp((t - (e.b - 0.2)) / 0.2);
  const drift = 1 + 0.05 * clamp((t - e.a) / Math.max(0.5, e.b - e.a));
  const fs = e.size || Math.min(400, 980 / (0.47 * e.text.length));
  return (
    <div style={{position: 'absolute', left: 0, right: 0, top: e.y || 690, display: 'flex', justifyContent: 'center', transform: 'translateY(-50%)'}}>
      <div style={{fontFamily: 'Bebas', fontSize: fs, lineHeight: 0.9, letterSpacing: 4, whiteSpace: 'nowrap', color: e.gold ? GOLD_L : '#fff', opacity: pin * (1 - pout), transform: `scale(${(1.25 - 0.25 * pin) * drift})`, textShadow: '0 0 60px rgba(212,171,114,0.35)'}}>{e.text}</div>
    </div>
  );
};
// vorne: kleine Zeile über dem großen Wort
const BehindKicker: React.FC<{t: number; e: Ev}> = ({t, e}) => {
  if (!e.kicker || !e.showKicker || t < e.a || t >= e.b) return null;
  const k = eo((t - e.a - 0.1) / 0.3) * (1 - clamp((t - (e.b - 0.2)) / 0.2));
  return (
    <div style={{position: 'absolute', left: 0, right: 0, top: 432, display: 'flex', justifyContent: 'center'}}>
      <div style={{fontFamily: 'Playfair', fontStyle: 'italic', fontWeight: 700, fontSize: 64, color: GOLD_L, opacity: k, transform: `translateY(${(1 - k) * 18}px)`, whiteSpace: 'nowrap', textShadow: '0 4px 18px rgba(0,0,0,0.9)'}}>{e.kicker}</div>
    </div>
  );
};
const GLIDE = 0.2;
const Camera: React.FC<{p: BProps}> = ({p}) => {
  const tn = useCurrentFrame() / FPS;
  const C = p.cam;
  const seg = (i: number, t: number) => {
    const a = C[i][0];
    const b = C[i + 1] ? C[i + 1][0] : p.dur;
    const u = clamp((t - a) / Math.max(0.01, b - a));
    return C[i][1] + (C[i][2] - C[i][1]) * u;
  };
  let i = 0;
  for (let k = 0; k < C.length; k++) if (tn >= C[k][0]) i = k;
  let z = seg(i, tn);
  const a = C[i][0];
  const b = C[i + 1] ? C[i + 1][0] : Infinity;
  if (i > 0 && tn - a < GLIDE) z = seg(i - 1, a) + (z - seg(i - 1, a)) * eio(0.5 + (tn - a) / (2 * GLIDE));
  else if (b - tn < GLIDE) z = z + (seg(i + 1, b) - z) * eio(0.5 - (b - tn) / (2 * GLIDE));
  const B = p.base;
  const F = frameAmt(tn, p.events);
  const L = (a: number, c: number) => a + (c - a) * F;
  const ox = L(0, CARD.x);
  const oy = L(0, CARD.y);
  return (
    <AbsoluteFill>
      {F > 0 ? <AbsoluteFill style={{background: 'radial-gradient(70% 45% at 50% 34%, rgba(212,171,114,0.22), transparent 70%)', opacity: F}} /> : null}
      <div style={{position: 'absolute', left: ox, top: oy, width: L(1080, CARD.w), height: L(1920, CARD.h), borderRadius: L(0, CARD.r), overflow: 'hidden', boxShadow: F > 0 ? `0 30px 80px rgba(0,0,0,${0.7 * F}), 0 0 0 ${2 * F}px rgba(212,171,114,${0.55 * F})` : 'none'}}>
        <div style={{position: 'absolute', left: -ox, top: -oy, width: 1080, height: 1920, transform: `translateY(${L(0, CARD.dy)}px) scale(${L(1, CARD.s)})`, transformOrigin: '540px 1180px'}}>
          <AbsoluteFill style={{transform: `scale(${z})`, transformOrigin: '540px 1150px'}}>
            <AbsoluteFill style={{transform: `translate(${B.tx}px, ${B.ty}px) scale(${B.s})`, transformOrigin: `${B.ox}px ${B.oy}px`}}>
              <OffthreadVideo src={staticFile(p.file)} style={{width: 1080, height: 1920}} />
            </AbsoluteFill>
            {p.events.map((e, i) => (e.type === 'behind' ? <BehindText key={i} t={tn} e={e} /> : null))}
            {p.events.map((e, i) =>
              e.type === 'behind' && e.cut ? (
                <Sequence key={`c${i}`} from={e.cf} durationInFrames={e.cn} layout="none">
                  <AbsoluteFill style={{transform: `translate(${B.tx}px, ${B.ty}px) scale(${B.s})`, transformOrigin: `${B.ox}px ${B.oy}px`}}>
                    <OffthreadVideo transparent muted src={staticFile(e.cut)} style={{width: 1080, height: 1920}} />
                  </AbsoluteFill>
                </Sequence>
              ) : null,
            )}
          </AbsoluteFill>
        </div>
      </div>
    </AbsoluteFill>
  );
};

const Sweep: React.FC<{t: number; at: number}> = ({t, at}) => {
  const u = (t - at) / 0.42;
  if (u < 0 || u > 1) return null;
  return (
    <AbsoluteFill style={{mixBlendMode: 'screen', opacity: Math.sin(u * Math.PI) * 0.34}}>
      <div style={{position: 'absolute', top: -300, bottom: -300, width: 520, left: -700 + u * 2300, transform: 'rotate(16deg)', background: `linear-gradient(90deg, transparent, ${GOLD} 45%, #fff 50%, ${GOLD} 55%, transparent)`, filter: 'blur(40px)'}} />
    </AbsoluteFill>
  );
};

const Outro: React.FC<{t: number; p: BProps}> = ({t, p}) => {
  const a = p.dur - 0.22;
  if (!p.outro || t < a) return null;
  const B = p.brand ?? {novus: true};
  const bg = clamp((t - a) / 0.25);
  const q = clamp((t - a - 0.15) / 0.9);
  const l1 = eo((t - a - 0.75) / 0.4);
  const l2 = eo((t - a - 1.0) / 0.4);
  const line = eo((t - a - 0.6) / 0.5);
  return (
    <AbsoluteFill style={{background: `rgba(8,8,9,${bg})`, alignItems: 'center', justifyContent: 'center'}}>
      <div style={{position: 'absolute', width: 1000, height: 700, background: 'radial-gradient(closest-side, rgba(212,171,114,0.2), transparent)', opacity: q}} />
      <div style={{position: 'absolute', left: 0, top: 0, width: 1080, height: 1920}}><Funken t={t} at={a + 0.8} x={540} y={900} n={36} farbe={GOLD_L} weite={420} /></div>
      <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', marginTop: -120}}>
        {B.logo ? <img src={staticFile(B.logo)} style={{height: 220, opacity: q, borderRadius: 28}} /> : B.novus ? <Lockup h={190} p={q} /> : null}
        <div style={{height: 3, width: 420 * line, background: `linear-gradient(90deg, transparent, ${GOLD}, transparent)`, marginTop: 56}} />
        <div style={{fontFamily: 'Playfair', fontStyle: 'italic', fontWeight: 700, fontSize: 62, color: '#fff', marginTop: 46, whiteSpace: 'nowrap', opacity: l1, transform: `translateY(${(1 - l1) * 20}px)`}}>
          {p.outro.line} <span style={{color: GOLD_L}}>{p.outro.gold}</span>
        </div>
        <div style={{display: 'flex', gap: 18, marginTop: 40, opacity: l2, transform: `translateY(${(1 - l2) * 20}px)`}}>
          {(p.outro.handle ?? (B.novus ? '@_novusmedia' : '')) ? <div style={{...card, padding: '16px 30px', borderRadius: 20, fontFamily: 'Montserrat', fontWeight: 700, fontSize: 34, color: GOLD_L}}>{p.outro.handle ?? '@_novusmedia'}</div> : null}
          {(p.outro.site ?? (B.novus ? 'novus-media.de' : '')) ? <div style={{...card, padding: '16px 30px', borderRadius: 20, fontFamily: 'Montserrat', fontWeight: 700, fontSize: 34, color: '#fff'}}>{p.outro.site ?? 'novus-media.de'}</div> : null}
        </div>
      </div>
    </AbsoluteFill>
  );
};

const SFXF: Record<string, {file: string; lead: number; skip: number}> = {
  wh: {file: 'audio_woosh_1', lead: 0.25, skip: 0},
  po: {file: 'aodio_pop_1', lead: 0.09, skip: 0.6},
  cl: {file: 'audio_mouse_click', lead: 0.1, skip: 0},
};

export const Batch: React.FC<BProps> = (p) => {
  GOLD = p.theme?.accent ?? '#D4AB72';
  GOLD_L = p.theme?.accentL ?? '#F0D3A4';
  const t = useCurrentFrame() / FPS;
  const vf = Math.round(p.dur * FPS);
  const F = frameAmt(t, p.events);
  const FO = fullOn(t, p.events);
  return (
    <AbsoluteFill style={{background: '#060607'}}>
      <Sequence durationInFrames={vf} layout="none">
        <Camera p={p} />
      </Sequence>
      <AbsoluteFill style={{opacity: 1 - F, background: 'linear-gradient(180deg, rgba(6,6,7,0.97) 0%, rgba(6,6,7,0.9) 14%, rgba(6,6,7,0.8) 24%, rgba(6,6,7,0.4) 33%, transparent 42%)'}} />
      <AbsoluteFill style={{opacity: 1 - F, background: 'radial-gradient(120% 80% at 50% 62%, transparent 55%, rgba(0,0,0,0.55) 100%)'}} />
      {p.look === 'film' ? <Sequence durationInFrames={vf} layout="none"><Filmlook t={t} farbe={GOLD} /></Sequence> : null}
      {p.sweeps.map((c) => (
        <Sweep key={c} t={t} at={c - 0.21} />
      ))}
      {(p.blitz || []).map((c) => (
        <Blitz key={`b${c}`} t={t} at={c} farbe={GOLD_L} />
      ))}
      {p.events.map((e, i) => {
        const R = RENDER[e.type];
        if (!R) return null;
        if (e.frame) {
          // Grafik unter der Sprecher-Karte, größer als auf der Bühne
          return (
            <AbsoluteFill key={i} style={{transform: `translateY(${1262 - 418}px) scale(${e.pscale || 1.15})`, transformOrigin: '540px 418px'}}>
              <R t={t} e={e} />
            </AbsoluteFill>
          );
        }
        return <R key={i} t={t} e={e} />;
      })}
      {p.events.map((e, i) => (e.type === 'full' ? <Full key={`f${i}`} t={t} e={e} /> : e.type === 'broll' ? <BrollFull key={`f${i}`} t={t} e={e} /> : e.type === 'phone' ? <Phone key={`f${i}`} t={t} e={e} /> : null))}
      <Badge t={t} dur={p.dur} hide={FO?.bg === 'paper'} brand={p.brand ?? {novus: true}} />
      <Captions t={t} chunks={p.chunks} dur={p.dur} y={FO ? (FO.type === 'phone' ? 1480 : 1370) : behindOn(t, p.events) ? 450 : 586 + (1062 - 586) * F} paper={FO?.bg === 'paper'} />
      <Outro t={t} p={p} />
      {p.sfx.map((s, i) => {
        const f = SFXF[s.k];
        return (
          <Sequence key={i} from={Math.max(0, Math.round((s.t - f.lead) * FPS))} layout="none">
            <Audio src={staticFile(`sfx/${f.file}.mp3`)} volume={s.vol} startFrom={Math.round(f.skip * FPS)} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
