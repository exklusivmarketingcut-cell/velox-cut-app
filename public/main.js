// ---------- nav ----------
const nav = document.getElementById('nav');
const onScroll = () => nav.classList.toggle('scrolled', window.scrollY > 20);
window.addEventListener('scroll', onScroll, { passive: true });
onScroll();
const burger = document.getElementById('burger');
burger.addEventListener('click', () => burger.setAttribute('aria-expanded', nav.classList.toggle('open')));
nav.querySelectorAll('.nav-links a, .nav-cta a').forEach(a => a.addEventListener('click', () => { nav.classList.remove('open'); burger.setAttribute('aria-expanded', 'false'); }));

// ---------- reveal on scroll ----------
const io = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); }
  });
}, { threshold: 0.15, rootMargin: '0px 0px -40px 0px' });
document.querySelectorAll('.reveal').forEach((el, i) => {
  el.style.transitionDelay = (el.closest('.hero3d') ? i * 0.08 : 0) + 's';
  io.observe(el);
});

// ---------- Standbild hinter jedes Video legen (Fallback, falls es nicht abspielt) ----------
document.querySelectorAll('video[poster]').forEach(v => { v.style.backgroundImage = `url("${v.getAttribute('poster')}")`; });

// ---------- demo-videos nur abspielen, wenn sichtbar ----------
// Manche Handys blockieren Autoplay (iPhone-Stromsparmodus, Android-Datensparmodus, In-App-Browser):
// dann bleibt das Standbild stehen und ein Play-Button erscheint
const needTap = v => {
  const box = v.parentElement;
  if (!box || box.querySelector(':scope > .tap-play') || v.classList.contains('bg-vid') || v.classList.contains('blurred')) return;
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'tap-play';
  b.setAttribute('aria-label', 'Video abspielen');
  b.addEventListener('click', ev => { ev.stopPropagation(); v.play().then(() => b.remove()).catch(() => {}); });
  box.appendChild(b);
};
const tryPlay = v => {
  v.muted = true; // manche In-App-Browser ignorieren das muted-Attribut
  const p = v.play();
  // Seite im Hintergrund geöffnet (z. B. neuer Tab) → erst beim Sichtbarwerden erneut starten, statt Play-Button
  if (p) p.then(() => v.parentElement?.querySelector(':scope > .tap-play')?.remove())
    .catch(() => (document.hidden ? document.addEventListener('visibilitychange', () => tryPlay(v), { once: true }) : needTap(v)));
};
const vio = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) tryPlay(e.target);
    else e.target.pause();
  });
}, { threshold: 0.1 });
// nur die stummen Vorschau-Videos – nicht das Abspiel-Fenster der Beispiele (das soll Ton haben)
document.querySelectorAll('video:not(#lbVideo)').forEach(v => vio.observe(v));

// ---------- billing toggle ----------
document.querySelectorAll('.bill').forEach(btn => btn.addEventListener('click', () => {
  document.querySelectorAll('.bill').forEach(b => b.classList.toggle('active', b === btn));
  const y = btn.dataset.bill === 'y';
  document.querySelectorAll('b[data-m]').forEach(p => { p.textContent = y ? p.dataset.y : p.dataset.m; });
  document.querySelectorAll('.yearly-note').forEach(n => { n.textContent = y ? n.dataset.y : (n.dataset.m || ''); });
  // Paket-Buttons: gewähltes Intervall mit zur Anmeldung geben
  document.querySelectorAll('.plan-cta[data-plan]:not([data-plan="free"])').forEach(b => {
    b.href = `/registrieren?paket=${b.dataset.plan}&intervall=${y ? 'yearly' : 'monthly'}`;
  });
}));

// ---------- caption studio ----------
// * markiert ein hervorgehobenes Wort
const SCRIPT = [
  ['Ich', 'hab', 'meine', '*Miete'],
  ['mit', '*Thunfischdosen'],
  ['bezahlt.'],
  ['Kein', 'Witz,', '*30', '*Dosen'],
  ['jeden', 'Monat.'],
];
// n = Wörter pro Einblendung, d = Verzögerung pro Wort (s), kara = Wort-für-Wort-Highlight, letters = Buchstaben einzeln (s pro Buchstabe)
const CFG = {
  rise: { n: 4, d: .2 }, box: { n: 3, d: .2 }, clean: { n: 4, kara: 1 }, hormozi: { n: 3, kara: 1 },
  kinetic: { n: 2, d: .2 }, neon: { n: 3, d: .22 }, glitch: { n: 2, d: .25 }, type: { n: 4, letters: .045 },
  marker: { n: 3, d: .22 }, comic: { n: 2, d: .22 }, drop: { n: 3, d: .14 }, outline: { n: 2, d: .3 },
  wave: { n: 3, letters: .035 }, flip: { n: 3, d: .15 }, gradient: { n: 3, d: .18 }, cinema: { n: 3, d: .28 },
  serif: { n: 4, d: .16 }, script: { n: 3, d: .2 }, slide: { n: 2, d: .18 }, retro: { n: 2, d: .2 },
};
const layer = document.getElementById('capLayer');
let timer = null, subTimers = [];

// Schriften vorladen, damit die Breitenmessung stimmt
['400 20px Bangers', '400 20px "Bebas Neue"', '900 20px Montserrat', '400 20px Pacifico', '400 20px "Permanent Marker"',
 '400 20px "Rubik Mono One"', '400 20px Anton', '800 20px "Bricolage Grotesque"', 'italic 700 20px "Playfair Display"']
  .forEach(f => document.fonts && document.fonts.load(f).catch(() => {}));

function phrases(style) {
  const n = CFG[style].n;
  const out = [];
  SCRIPT.forEach(line => { for (let i = 0; i < line.length; i += n) out.push(line.slice(i, i + n)); });
  return out;
}

// zu breite Wörter (z. B. „Thunfischdosen“ in breiten Schriften) passend verkleinern
function fit(cap, spans) {
  cap.style.fontSize = '';
  const max = layer.clientWidth * 0.86;
  const widest = Math.max(...spans.map(s => s.offsetWidth));
  if (widest > max) cap.style.fontSize = parseFloat(getComputedStyle(cap).fontSize) * max / widest * 0.97 + 'px';
}

function play() {
  clearTimeout(timer);
  subTimers.forEach(clearTimeout); subTimers = [];
  const style = layer.dataset.style;
  const cfg = CFG[style];
  const list = phrases(style);
  let p = 0;
  const step = () => {
    subTimers.forEach(clearTimeout); subTimers = [];
    const words = list[p % list.length];
    const cap = document.createElement('div');
    cap.className = 'cap';
    let li = 0, end = 0;
    const spans = words.map((raw, i) => {
      const s = document.createElement('span');
      s.className = 'w' + (raw.startsWith('*') ? ' e' : '');
      const txt = raw.replace('*', '');
      if (cfg.letters) {
        [...txt].forEach(ch => {
          const l = document.createElement('span');
          l.className = 'l';
          l.textContent = ch;
          l.style.animationDelay = (li++ * cfg.letters).toFixed(3) + 's';
          s.appendChild(l);
        });
        li += 2;
        end = li * cfg.letters * 1000;
      } else {
        s.textContent = txt;
        if (!cfg.kara) s.style.animationDelay = (i * cfg.d) + 's';
        end = cfg.kara ? words.length * 230 : i * cfg.d * 1000 + 450;
      }
      cap.appendChild(s);
      return s;
    });
    layer.replaceChildren(cap);
    fit(cap, spans);
    if (cfg.kara) {
      spans.forEach((s, i) => subTimers.push(setTimeout(() => {
        spans.forEach(x => x.classList.remove('cur'));
        s.classList.add('on', 'cur');
      }, i * 230)));
    }
    p++;
    timer = setTimeout(step, Math.max(end + 700, words.length * 230 + 650));
  };
  step();
}

function setStyle(name) {
  document.querySelectorAll('.style-btn').forEach(b => b.classList.toggle('active', b.dataset.style === name));
  layer.dataset.style = name;
  play();
}
document.querySelectorAll('.style-btn').forEach(btn => btn.addEventListener('click', () => setStyle(btn.dataset.style)));

// Farben: Akzent (--acc) und Textfarbe (--tc), jeweils mit eigenem Farbwähler
function colorRow(rowSel, prop) {
  const row = document.querySelector(rowSel);
  if (!row) return () => {};
  const custom = row.querySelector('.sw-custom');
  const input = custom.querySelector('input');
  const set = (c, el) => {
    row.querySelectorAll('.sw').forEach(s => s.classList.toggle('active', s === el));
    layer.style.setProperty(prop, c);
  };
  row.querySelectorAll('button.sw').forEach(sw => sw.addEventListener('click', () => set(sw.dataset.color, sw)));
  input.addEventListener('input', () => { custom.style.setProperty('--c', input.value); custom.classList.add('picked'); set(input.value, custom); });
  return set;
}
const setAcc = colorRow('.sw-acc', '--acc');
colorRow('.sw-txt', '--tc');

document.getElementById('capDice')?.addEventListener('click', () => {
  // nur aus den gerade sichtbaren Stilen/Farben würfeln
  const all = document.getElementById('capCtl').classList.contains('all') ? '' : ':not(.extra)';
  const btns = [...document.querySelectorAll('.style-btn:not(.active)' + all)];
  const sws = [...document.querySelectorAll('.sw-acc button.sw:not(.active)' + all)];
  const sw = sws[Math.floor(Math.random() * sws.length)];
  setAcc(sw.dataset.color, sw);
  setStyle(btns[Math.floor(Math.random() * btns.length)].dataset.style);
});
play();

// ---------- hero: ein Feld für Link ODER eigene Datei ----------
const linkBar = document.getElementById('linkBar');
const linkInput = linkBar.querySelector('input[type="text"]');
const heroFile = document.getElementById('heroFile');
const setFile = f => { if (!f) return; linkInput.value = '📁 ' + f.name; linkBar.classList.add('has-file'); };
heroFile.addEventListener('change', () => setFile(heroFile.files[0]));
linkInput.addEventListener('input', () => linkBar.classList.remove('has-file'));
['dragenter', 'dragover'].forEach(ev => linkBar.addEventListener(ev, e => { e.preventDefault(); linkBar.classList.add('drag'); }));
['dragleave', 'drop'].forEach(ev => linkBar.addEventListener(ev, e => { e.preventDefault(); linkBar.classList.remove('drag'); }));
linkBar.addEventListener('drop', e => setFile(e.dataTransfer.files[0]));

// ---------- tool-kategorien ----------
const showCat = cat => {
  document.querySelectorAll('.tool').forEach(t => { t.hidden = t.dataset.cat !== cat; });
};
document.querySelectorAll('.tt').forEach(tab => tab.addEventListener('click', () => {
  document.querySelectorAll('.tt').forEach(x => x.classList.toggle('active', x === tab));
  showCat(tab.dataset.cat);
}));
showCat('clip');

// ---------- hero-formular: führt zur Anmeldung (Drive-Link wird fürs Studio gemerkt) ----------
document.getElementById('linkBar').addEventListener('submit', e => {
  e.preventDefault();
  const val = linkInput.value.trim();
  try {
    if (val && !linkBar.classList.contains('has-file')) sessionStorage.setItem('velox_link', val);
  } catch { /* privater Modus: dann eben ohne */ }
  location.href = '/registrieren';
});

// ---------- 3D-Handyfächer: klappt beim Scrollen auf ----------
const fanSec = document.querySelector('.fan-sec');
const fan = document.getElementById('fan');
const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
let fanTick = false;
const updateFan = () => {
  fanTick = false;
  const r = fanSec.getBoundingClientRect();
  const vh = window.innerHeight;
  // 0 = Abschnitt kommt unten rein, 1 = Abschnitt ist mittig
  const p = Math.min(1, Math.max(0, (vh - r.top) / (vh * 0.9)));
  const e = 1 - Math.pow(1 - p, 3);
  fan.style.setProperty('--rx', (38 - 38 * e).toFixed(2) + 'deg');
  fan.style.setProperty('--tz', (-220 + 220 * e).toFixed(1) + 'px');
  fan.style.setProperty('--spread', (0.35 + 0.65 * e).toFixed(3));
};
if (!reduce) {
  window.addEventListener('scroll', () => { if (!fanTick) { fanTick = true; requestAnimationFrame(updateFan); } }, { passive: true });
  window.addEventListener('resize', updateFan);
  updateFan();
}

// ---------- 3D-Tilt bei Hover ----------
if (!reduce && window.matchMedia('(hover: hover)').matches) {
  document.querySelectorAll('.tilt').forEach(el => {
    const max = el.classList.contains('phone') || el.classList.contains('pf') ? 10 : 6;
    el.addEventListener('pointermove', e => {
      const r = el.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
      el.style.setProperty('--tx', ((x - 0.5) * max * 2).toFixed(2) + 'deg');
      el.style.setProperty('--ty', ((0.5 - y) * max * 2).toFixed(2) + 'deg');
      el.style.setProperty('--gx', (x * 100).toFixed(1) + '%');
      el.style.setProperty('--gy', (y * 100).toFixed(1) + '%');
      el.classList.add('is-tilting');
    });
    el.addEventListener('pointerleave', () => {
      el.classList.remove('is-tilting');
      el.style.setProperty('--tx', '0deg');
      el.style.setProperty('--ty', '0deg');
    });
  });
}

// ---------- Portfolio-Lightbox ----------
const lb = document.getElementById('lightbox');
const lbVideo = document.getElementById('lbVideo');
const closeLb = () => { lbVideo.pause(); lbVideo.removeAttribute('src'); lbVideo.load(); lb.hidden = true; };
// Delegation, damit auch die Kopien im Endlos-Karussell funktionieren
document.addEventListener('click', e => {
  const b = e.target.closest('.pf');
  if (!b) return;
  lbVideo.src = b.dataset.src;
  lb.hidden = false;
  // mit Ton auf halber Lautstärke starten (iPhones regeln die Lautstärke nur über die Tasten)
  lbVideo.muted = false;
  lbVideo.volume = 0.5;
  lbVideo.play().catch(() => { lbVideo.muted = true; lbVideo.play().catch(() => {}); });
});
lb.addEventListener('click', e => { if (e.target === lb || e.target.closest('.lb-close')) closeLb(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !lb.hidden) closeLb(); });

// ---------- Preise: Studio / Service ----------
document.querySelectorAll('.prod').forEach(btn => btn.addEventListener('click', () => {
  document.querySelectorAll('.prod').forEach(b => b.classList.toggle('active', b === btn));
  document.querySelectorAll('.price-pane').forEach(p => { p.hidden = p.dataset.pane !== btn.dataset.prod; });
}));

// ---------- Kontakt ----------
const topicSelect = document.getElementById('topicSelect');
function setTopic(t) { if (t && topicSelect.querySelector(`option[value="${t}"]`)) topicSelect.value = t; }
document.querySelectorAll('[data-topic]').forEach(a => a.addEventListener('click', () => setTopic(a.dataset.topic)));

const SUBJECTS = {
  beta: 'Beta-Zugang Velox Cut Studio',
  other: 'Frage zu Velox Cut Studio',
};
const mailFallback = (f, name, email) => {
  const body = `Name: ${name}\nE-Mail: ${email}\n\n${f.elements.msg.value.trim()}`;
  window.location.href = 'mailto:infoveloxcut@gmail.com?subject=' + encodeURIComponent(SUBJECTS[f.elements.topic.value]) + '&body=' + encodeURIComponent(body);
};
// Anfrage an die App (/api/beta); lokal (file://) oder bei Fehlern -> E-Mail-Programm
document.getElementById('contactForm').addEventListener('submit', async e => {
  e.preventDefault();
  const f = e.target;
  const name = f.elements.name.value.trim(), email = f.elements.email.value.trim();
  const err = document.getElementById('formErr');
  if (!name || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) { err.hidden = false; return; }
  err.hidden = true;
  if (location.protocol === 'file:') return mailFallback(f, name, email);
  const btn = f.querySelector('button[type="submit"]');
  btn.disabled = true;
  try {
    const r = await fetch('/api/beta', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.fromEntries(new FormData(f))) });
    if (!r.ok) throw new Error(r.status);
    f.querySelectorAll('label, .row2, button, .form-note').forEach(el => el.hidden = true);
    document.getElementById('formOk').hidden = false;
  } catch {
    btn.disabled = false;
    mailFallback(f, name, email);
  }
});

// ---------- Portfolio-Coverflow: Endlosschleife, Karten drehen sich zur Mitte ----------
const rail = document.getElementById('pfRail');
const originals = [...rail.querySelectorAll('.pf')];
const N = originals.length;
// je ein kompletter Satz Kopien links und rechts -> man kommt nie ans Ende
const mkClone = c => { const k = c.cloneNode(true); k.setAttribute('aria-hidden', 'true'); k.tabIndex = -1; return k; };
originals.slice().reverse().forEach(c => rail.prepend(mkClone(c)));
originals.forEach(c => rail.append(mkClone(c)));
const cards = [...rail.querySelectorAll('.pf')];
const railStep = () => cards[1].offsetLeft - cards[0].offsetLeft;
const setWidth = () => railStep() * N;
let railTick = false;
const updateRail = () => {
  railTick = false;
  const rr = rail.getBoundingClientRect();
  const mid = rr.left + rr.width / 2;
  cards.forEach(c => {
    const r = c.getBoundingClientRect();
    if (r.right < rr.left - 400 || r.left > rr.right + 400) return;
    const d = (r.left + r.width / 2 - mid) / r.width;
    const a = Math.max(-2.2, Math.min(2.2, d));
    c.style.setProperty('--ry', (reduce ? 0 : -a * 24).toFixed(1) + 'deg');
    c.style.setProperty('--sc', (1 - Math.min(Math.abs(a), 2) * 0.09).toFixed(3));
    c.style.setProperty('--op', (1 - Math.min(Math.abs(a), 2.2) * 0.22).toFixed(3));
    c.classList.toggle('center', Math.abs(d) < 0.5);
  });
};
// liegt die Mitte in einem Kopien-Satz, unsichtbar um genau einen Satz zurückspringen
const wrapAround = () => {
  const w = setWidth();
  if (rail.scrollLeft < w * 0.5) rail.scrollLeft += w;
  else if (rail.scrollLeft > w * 1.5) rail.scrollLeft -= w;
  updateRail();
};
let settle;
rail.addEventListener('scroll', () => {
  if (!railTick) { railTick = true; requestAnimationFrame(updateRail); }
  clearTimeout(settle);
  settle = setTimeout(wrapAround, 140);
}, { passive: true });
const startPos = () => { rail.scrollLeft = setWidth() + railStep() * 2; updateRail(); };
window.addEventListener('resize', updateRail);
// Start im mittleren (echten) Satz
requestAnimationFrame(startPos);

// ---------- Für wen? Zielgruppen-Umschalter ----------
const audChips = document.querySelectorAll('.aud-chip');
function showAud(key, scroll) {
  const chip = document.querySelector(`.aud-chip[data-aud="${key}"]`);
  if (!chip) return;
  audChips.forEach(c => { const on = c === chip; c.classList.toggle('active', on); c.setAttribute('aria-selected', on); });
  document.querySelectorAll('.aud-panel').forEach(p => { p.hidden = p.dataset.aud !== key; });
  // auf dem Handy liegt die Ansicht unter den Karten -> hinscrollen
  const panel = document.getElementById('aud-' + key);
  if (scroll) document.getElementById('fuer-wen').scrollIntoView({ behavior: 'smooth' });
  else if (window.innerWidth <= 900) window.scrollTo({ top: panel.getBoundingClientRect().top + window.scrollY - 80, behavior: 'smooth' });
}
audChips.forEach(c => c.addEventListener('click', () => showAud(c.dataset.aud)));
document.querySelectorAll('a[data-aud]').forEach(a => a.addEventListener('click', e => {
  e.preventDefault();
  document.activeElement?.blur();
  document.getElementById('nav')?.classList.remove('open');
  showAud(a.dataset.aud, true);
}));

// ---------- Navigation: aktuellen Abschnitt hervorheben ----------
const navMap = [...document.querySelectorAll('.nav-links > a, .nav-dd-btn')]
  .map(a => [a, document.querySelector(a.getAttribute('href'))]).filter(([, s]) => s);
function spy() {
  const y = window.innerHeight * 0.35;
  let cur = null;
  navMap.forEach(([a, s]) => { const r = s.getBoundingClientRect(); if (r.top <= y && r.bottom > y) cur = a; });
  navMap.forEach(([a]) => a.classList.toggle('active', a === cur));
}
window.addEventListener('scroll', spy, { passive: true });
spy();

// ---------- Handy: feste CTA-Leiste unten, sobald der Hero vorbei ist ----------
const sticky = document.getElementById('stickyCta');
if (sticky) {
  const hide = [document.getElementById('start'), document.getElementById('kontakt'), document.querySelector('.final'), document.querySelector('.footer')];
  const seen = new Set();
  const sio = new IntersectionObserver(es => {
    es.forEach(e => e.isIntersecting ? seen.add(e.target) : seen.delete(e.target));
    const on = seen.size === 0;
    sticky.classList.toggle('show', on);
    sticky.setAttribute('aria-hidden', !on);
    sticky.querySelector('a').tabIndex = on ? 0 : -1;
  });
  hide.filter(Boolean).forEach(el => sio.observe(el));
}

// ---------- Untertitel-Studio: weitere Stile & Farben erst auf Wunsch ----------
const capMore = document.getElementById('capMore');
capMore?.addEventListener('click', () => {
  const open = document.getElementById('capCtl').classList.toggle('all');
  capMore.setAttribute('aria-expanded', open);
  capMore.textContent = open ? '− Weniger zeigen' : '+ Alle 20 Stile & Farben';
});

// ---------- Preis-Rechner: was kostet Schnitt heute vs. Velox Cut ----------
const calcRange = document.getElementById('calcRange');
if (calcRange) {
  // günstigstes Paket, dessen Kontingent reicht
  // Credits: 10 pro Kurzvideo. Günstigste Kombination aus Paket + Nachkauf-Paketen
  const PLANS = [['Free', 0, 10], ['Starter', 14.9, 200], ['Pro', 29.9, 500], ['Business', 59.9, 900]];
  const packCost = need => { // günstigste Nachkauf-Mischung für >= need Credits
    if (need <= 0) return [0, 0];
    let best = [Infinity, 0];
    for (let c = 0; c <= Math.ceil(need / 600); c++)
      for (let b = 0; b <= Math.ceil(need / 300); b++) {
        const rest = Math.max(0, need - c * 600 - b * 300), a = Math.ceil(rest / 100);
        const cost = c * 49.9 + b * 27.9 + a * 9.9;
        if (cost < best[0] - 1e-9) best = [cost, c * 600 + b * 300 + a * 100];
      }
    return best;
  };
  const pick = (n, per) => {
    let best = null;
    for (const [name, price, cr] of PLANS) {
      const [extra, extraCr] = packCost(n * per - cr), total = price + extra;
      if (!best || total < best[1] - 1e-9) best = [extraCr ? name + ' + ' + extraCr + ' Credits' : name, total];
    }
    return best;
  };
  const eur = v => v.toLocaleString('de-DE', { minimumFractionDigits: v % 1 ? 2 : 0, maximumFractionDigits: 2 }) + ' €';
  const $ = id => document.getElementById(id);
  let st = {cr: 10, ed: 25, h: 1};
  const hrs = h => (Math.round(h * 10) / 10).toLocaleString('de-DE');
  const upd = () => {
    const n = +calcRange.value;
    const [name, price] = pick(n, st.cr);
    const editor = n * st.ed;
    $('calcN').textContent = n;
    $('calcHours').textContent = hrs(n * st.h) + ' Std.';
    $('calcEditor').textContent = eur(editor);
    $('calcPlan').textContent = name;
    $('calcPrice').textContent = eur(price);
    $('calcSave').textContent = eur(Math.round(editor - price));
    $('calcSaveH').textContent = hrs(n * st.h) + ' Stunden';
    calcRange.style.setProperty('--p', ((n - calcRange.min) / (calcRange.max - calcRange.min) * 100) + '%');
  };
  calcRange.addEventListener('input', upd);
  document.querySelectorAll('.calc-style .cs').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('.calc-style .cs').forEach(x => x.classList.toggle('active', x === b));
    st = {cr: +b.dataset.cr, ed: +b.dataset.ed, h: +b.dataset.h};
    upd();
  }));
  upd();
}

// ---------- Schnitt-Stile: derselbe Clip als Clean / Viral / Premium ----------
const styleVid = document.getElementById('styleVid');
if (styleVid) {
  const STYLES = {
    clean: ['Clean', 'Sauberer Schnitt, Untertitel, leichte Zooms'],
    viral: ['Viral', 'Zoom-Punches, Sounds, Emojis, Hook-Titel'],
    premium: ['Premium', '3D-Titel, Motion Graphics, Zähler – kommt bald als Stil'],
  };
  document.querySelectorAll('.style-pick .sp').forEach(b => b.addEventListener('click', () => {
    const k = b.dataset.style;
    document.querySelectorAll('.style-pick .sp').forEach(x => { x.classList.toggle('active', x === b); x.setAttribute('aria-selected', x === b); });
    styleVid.poster = `assets/demo/style_${k}.jpg`;
    styleVid.style.backgroundImage = `url("assets/demo/style_${k}.jpg")`;
    styleVid.src = `https://media.velox-cut.de/assets/demo/style_${k}.mp4`;
    tryPlay(styleVid);
    document.getElementById('styleCap').innerHTML = `<b>${STYLES[k][0]}</b> ${STYLES[k][1]}`;
  }));
}
