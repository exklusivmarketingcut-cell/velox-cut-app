'use client';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import CaptionPreview from './CaptionPreview';
import Results from './Results';
import { jobCost, MAX_CLIPS_PER_JOB, MAX_UPLOAD_BYTES, STYLES, type StyleId } from '@/lib/pricing';

type Job = {
  id: string; status: 'queued' | 'processing' | 'done' | 'failed'; style: StyleId; clips: number; source_name: string | null;
  source_kind: string; credits_charged: number; progress: number; error: string | null; created_at: string;
  outputs: { title?: string; songs?: { song: string; artist: string; warum?: string }[]; post?: { text: string; hashtags: string[] } | null }[];
  started_at?: string | null; stage?: string | null;
};

const CAPTIONS = [
  ['rise', 'Rise'], ['hormozi', 'Pop'], ['clean', 'Karaoke'], ['neon', 'Neon'], ['comic', 'Comic'], ['serif', 'Serif'],
] as const;
const CAPTION_HINT: Record<string, string> = {
  rise: 'weich, modern', hormozi: 'groß, knallig', clean: 'Wort für Wort', neon: 'leuchtend', comic: 'verspielt', serif: 'elegant',
};
const COLORS = ['#F6E27F', '#FF3D7F', '#4F9EFF', '#3DFF9A', '#FF7A3D', '#FFFFFF'];
/** Restzeit grob aus bisherigem Tempo schätzen (erst ab 15 %, vorher „wird berechnet“) */
function eta(j: Job): string {
  if (!j.started_at || j.progress < 15) return 'Restzeit wird berechnet …';
  const elapsed = (Date.now() - new Date(j.started_at).getTime()) / 1000;
  const left = Math.max(10, (elapsed * (100 - j.progress)) / j.progress);
  return left < 60 ? 'noch ca. 1 Min.' : `noch ca. ${Math.round(left / 60)} Min.`;
}

const STATUS: Record<Job['status'], string> = { queued: 'In der Warteschlange', processing: 'Wird geschnitten', done: 'Fertig', failed: 'Fehlgeschlagen' };

/** Datei direkt in den Speicher hochladen (signierter Link, mit Fortschritt) */
function putFile(signedUrl: string, file: File, onProgress: (p: number) => void) {
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', signedUrl);
    xhr.setRequestHeader('Content-Type', file.type || 'video/mp4');
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => (xhr.status < 300 ? resolve() : reject(new Error(String(xhr.status))));
    xhr.onerror = () => reject(new Error('network'));
    xhr.send(file);
  });
}

const MEDIA = (f: File) => f.type.startsWith('video/') || f.type.startsWith('image/') || /\.(mp4|mov|m4v|mts|jpe?g|png|webp|heic)$/i.test(f.name);

/** Gezogene Dateien UND Ordner (auch Unterordner) einsammeln */
async function droppedFiles(dt: DataTransfer): Promise<File[]> {
  type Entry = { isFile: boolean; isDirectory: boolean; file?: (cb: (f: File) => void, err: () => void) => void;
    createReader?: () => { readEntries: (cb: (e: Entry[]) => void, err: () => void) => void } };
  const items = Array.from(dt.items ?? []).map((i) => (i as unknown as { webkitGetAsEntry?: () => Entry | null }).webkitGetAsEntry?.() ?? null);
  if (!items.some(Boolean)) return Array.from(dt.files);
  const out: File[] = [];
  const walk = async (e: Entry): Promise<void> => {
    if (e.isFile && e.file) out.push(await new Promise<File>((res, rej) => e.file!(res, rej)));
    else if (e.isDirectory && e.createReader) {
      const r = e.createReader();
      for (;;) {
        const batch = await new Promise<Entry[]>((res, rej) => r.readEntries(res, rej));
        if (!batch.length) break;
        for (const x of batch) await walk(x);
      }
    }
  };
  for (const e of items) if (e) await walk(e);
  return out;
}

/** Link, den jemand auf der Startseite eingefügt hat (wird dort in sessionStorage gemerkt) */
const readHeroLink = () => {
  try { return sessionStorage.getItem('velox_link'); } catch { return null; }
};
const noSubscribe = () => () => {};
const DRIVE_RE = /^https:\/\/((drive|docs)\.google\.com|(www\.|m\.)?youtube\.com|youtu\.be)\//; // Links, die wir öffnen können: Google Drive, YouTube

export default function Studio({ credits, initialJobs }: { credits: number; initialJobs: Job[] }) {
  const [tab, setTab] = useState<'upload' | 'drive'>('upload');
  const [files, setFiles] = useState<File[]>([]);
  const file = files.find((f) => f.type.startsWith('video/') || /\.(mp4|mov|m4v)$/i.test(f.name)) ?? null; // Vorschau: erstes Video
  const [drive, setDrive] = useState('');
  const [style, setStyle] = useState<StyleId>('clean');
  const [caption, setCaption] = useState('rise');
  const [color, setColor] = useState(COLORS[0]);
  const [clips, setClips] = useState(1);
  const [useLogo, setUseLogo] = useState(false);
  const [logoUrl, setLogoUrl] = useState<string | null>(null);
  const [logoBusy, setLogoBusy] = useState(false);
  const [music, setMusic] = useState<'none' | 'auto'>('none');
  const [brand, setBrand] = useState('');
  const brandSaved = useRef('');
  const logoInput = useRef<HTMLInputElement>(null);
  const [up, setUp] = useState(-1);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<React.ReactNode>('');
  const [jobs, setJobs] = useState<Job[]>(initialJobs);
  // Ergebnis-Ansicht: der neueste fertige Auftrag ist aufgeklappt, wenn er höchstens einen Tag alt ist
  const [open, setOpen] = useState<string | null>(() => {
    const j = initialJobs.find((x) => x.status === 'done');
    return j && Date.now() - new Date(j.created_at).getTime() < 864e5 ? j.id : null;
  });
  const [online, setOnline] = useState<boolean | null>(null);
  const [balance, setBalance] = useState(credits);
  const [over, setOver] = useState(false);
  const heroLink = useSyncExternalStore(noSubscribe, readHeroLink, () => null);
  const [heroDone, setHeroDone] = useState(false);
  const dropHeroLink = (use: boolean) => {
    if (use && heroLink) { setTab('drive'); setDrive(heroLink); }
    try { sessionStorage.removeItem('velox_link'); } catch { /* egal */ }
    setHeroDone(true);
  };
  const input = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const router = useRouter();
  const failedSeen = useRef(new Set(initialJobs.filter((j) => j.status === 'failed').map((j) => j.id)));

  const cost = jobCost(style, clips);
  const reload = useCallback(async () => {
    const r = await fetch('/api/jobs', { cache: 'no-store' });
    if (!r.ok) return;
    const d = await r.json();
    const next: Job[] = d.jobs;
    setJobs(next);
    setOnline(d.online ?? null);
    // Neu fehlgeschlagen → Credits wurden erstattet: Stand oben neu laden
    const fresh = next.filter((j) => j.status === 'failed' && !failedSeen.current.has(j.id));
    fresh.forEach((j) => failedSeen.current.add(j.id));
    if (fresh.length) router.refresh();
  }, [router]);
  const active = jobs.some((j) => j.status === 'queued' || j.status === 'processing');
  useEffect(() => {
    if (!active) return;
    const t = setInterval(reload, 5000);
    return () => clearInterval(t);
  }, [active, reload]);

  // Logo aus dem Konto laden (für die Vorschau)
  useEffect(() => {
    fetch('/api/logo').then((r) => (r.ok ? r.json() : null)).then((d) => d && setLogoUrl(d.url)).catch(() => null);
    fetch('/api/marken').then((r) => (r.ok ? r.json() : null)).then((d) => { if (d) { setBrand(d.words); brandSaved.current = d.words; } }).catch(() => null);
  }, []);

  async function uploadLogo(f?: File | null) {
    if (!f) return;
    setErr('');
    setLogoBusy(true);
    try {
      const s = await fetch('/api/logo', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ size: f.size, type: f.type }) }).then((r) => r.json());
      if (!s.signedUrl) throw new Error(s.error || 'Logo konnte nicht hochgeladen werden.');
      await putFile(s.signedUrl, f, () => {});
      const d = await fetch('/api/logo', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: s.path }) }).then((r) => r.json());
      if (d.error) throw new Error(d.error);
      setLogoUrl(d.url);
      setUseLogo(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Logo konnte nicht hochgeladen werden.');
    } finally {
      setLogoBusy(false);
    }
  }

  /** Namen & Marken speichern (einmal im Konto, gilt für alle Videos) */
  async function saveBrand() {
    const w = brand.trim();
    if (w === brandSaved.current) return;
    const r = await fetch('/api/marken', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ words: w }) }).catch(() => null);
    if (r?.ok) brandSaved.current = w;
  }

  function pick(list?: FileList | File[] | null) {
    setErr('');
    const all = Array.from(list ?? []).filter((f) => !f.name.startsWith('.') && MEDIA(f));
    if (!all.length) return setErr('Bitte wähle Videos (und gern passende Bilder) aus.');
    if (!all.some((f) => f.type.startsWith('video/') || /\.(mp4|mov|m4v|mts)$/i.test(f.name))) return setErr('Es muss mindestens ein Video dabei sein.');
    if (all.some((f) => f.size > MAX_UPLOAD_BYTES)) return setErr('Eine Datei ist größer als 4 GB – bitte als Google-Drive-Link einfügen.');
    if (all.length > 300) return setErr('Bitte höchstens 300 Dateien auf einmal.');
    setFiles(all);
  }

  async function start() {
    setErr('');
    if (cost > balance)
      return setErr(<>Dafür brauchst du {cost} Credits, du hast {balance}. <Link href="/konto#nachkauf">Credits nachkaufen →</Link></>);
    if (useLogo && !logoUrl) return setErr('Bitte lade zuerst dein Logo hoch – oder nimm den Haken bei „Logo einbauen“ raus.');
    await saveBrand();
    let sourcePath: string | undefined;
    let sourceName: string | undefined;
    setBusy(true);
    try {
      let sourcePaths: string[] | undefined;
      if (tab === 'upload' && files.length > 1) {
        // mehrere Clips (Ordner) → gemeinsamer Stapel, Gesamtfortschritt über alle Bytes
        const batch = crypto.randomUUID();
        const total = files.reduce((a, f) => a + f.size, 0);
        let done = 0;
        sourcePaths = [];
        setUp(0);
        for (const f of files) {
          const type = f.type || (/\.(jpe?g)$/i.test(f.name) ? 'image/jpeg' : /\.png$/i.test(f.name) ? 'image/png' : 'video/mp4');
          const s = await fetch('/api/uploads/sign', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: f.name, size: f.size, type, batch }),
          }).then((r) => r.json());
          if (!s.signedUrl) throw new Error(s.error || `${f.name} konnte nicht hochgeladen werden.`);
          await putFile(s.signedUrl, f, (p) => setUp(Math.round(((done + (f.size * p) / 100) / total) * 100)));
          done += f.size;
          sourcePaths.push(s.path);
        }
        sourceName = `${files.length} Dateien`;
      } else if (tab === 'upload') {
        if (!file) throw new Error('Bitte wähle zuerst ein Video aus.');
        const s = await fetch('/api/uploads/sign', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: file.name, size: file.size, type: file.type || 'video/mp4' }),
        }).then((r) => r.json());
        if (!s.signedUrl) throw new Error(s.error || 'Upload konnte nicht vorbereitet werden.');
        setUp(0);
        await putFile(s.signedUrl, file, setUp);
        sourcePath = s.path;
        sourceName = file.name;
      } else if (!/^https:\/\/((drive|docs)\.google\.com|(www\.|m\.)?youtube\.com|youtu\.be)\//.test(drive.trim())) {
        throw new Error('Bitte füge einen YouTube- oder Google-Drive-Link ein (Drive-Freigabe: „Jeder mit dem Link“).');
      } else sourceName = /youtu\.?be/.test(drive) ? 'YouTube-Video' : /\/folders\//.test(drive) ? 'Google-Drive-Ordner' : 'Google Drive';
      const r = await fetch('/api/jobs', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sourceKind: sourcePaths ? 'folder' : tab, sourcePath, sourcePaths, driveUrl: tab === 'drive' ? drive.trim() : undefined, sourceName, style, captionStyle: caption, captionColor: color, clips, useLogo, music }),
      });
      const d = await r.json();
      if (!r.ok) {
        if (d.error === 'credits') throw new Error(`Dafür brauchst du ${d.need} Credits, du hast ${d.have}.`);
        throw new Error(d.error || 'Das hat nicht geklappt.');
      }
      setBalance((b) => b - d.cost);
      router.refresh(); // Credit-Anzeige oben aktualisieren
      setFiles([]);
      setDrive('');
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Das hat nicht geklappt.');
    } finally {
      setBusy(false);
      setUp(-1);
    }
  }

  async function download(id: string) {
    const d = await fetch(`/api/jobs/${id}/files`).then((r) => r.json());
    (d.files ?? []).forEach((f: { url: string | null }, i: number) => f.url && setTimeout(() => (location.href = f.url!), i * 800));
  }

  return (
    <>
    <div className="studio-form">
      <section className="card stack">
        {heroLink && !heroDone ? (
          DRIVE_RE.test(heroLink) ? (
            <div className="hint">
              <span>Dein Link von der Startseite ist bereit.</span>
              <button type="button" className="btn" onClick={() => dropHeroLink(true)}>Übernehmen</button>
            </div>
          ) : (
            <div className="hint">
              <span>Diesen Link können wir noch nicht direkt öffnen. Lade das Video als Datei hoch oder nutze einen YouTube- oder Google-Drive-Link.</span>
              <button type="button" className="btn" onClick={() => dropHeroLink(false)}>Okay</button>
            </div>
          )
        ) : null}
        <div className="tabs" role="tablist">
          <button className={tab === 'upload' ? 'on' : ''} onClick={() => setTab('upload')} type="button">📁 Datei hochladen</button>
          <button className={tab === 'drive' ? 'on' : ''} onClick={() => setTab('drive')} type="button">Link (YouTube / Drive)</button>
        </div>
        {tab === 'upload' ? (
          <div
            className={`drop${over ? ' over' : ''}`}
            onClick={() => input.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }}
            onDragLeave={() => setOver(false)}
            onDrop={async (e) => { e.preventDefault(); setOver(false); pick(await droppedFiles(e.dataTransfer)); }}
          >
            <input ref={input} type="file" accept="video/*,image/*" multiple hidden onChange={(e) => pick(e.target.files)} />
            <b>{files.length > 1 ? `📂 ${files.length} Dateien` : file ? `🎬 ${file.name}` : 'Video – oder ganzen Ordner – hierher ziehen'}</b>
            <small>
              {files.length
                ? `${(files.reduce((a, f) => a + f.size, 0) / 1024 / 1024).toFixed(0)} MB${files.length > 1 ? ` · ${files.filter((f) => f.type.startsWith('image/')).length} Bilder` : ''}`
                : 'Ein Video oder mehrere Clips + Produktbilder – wir machen daraus EIN fertiges Video'}
            </small>
          </div>
        ) : null}
        {tab === 'upload' ? (
          <p className="note" style={{ margin: '-4px 0 0' }}>📁 <b>Ein Ordner = ein Video.</b> Lade pro Ordner bitte nur das Rohmaterial für <b>ein</b> Video hoch. Für mehrere Videos: je Video einen eigenen Ordner.</p>
        ) : null}
        {tab === 'upload' ? (
          <div className="row" style={{ gap: 8, marginTop: -6 }}>
            <button type="button" className="btn" style={{ padding: '6px 12px', fontSize: 13 }} onClick={() => input.current?.click()}>Dateien wählen</button>
            <button type="button" className="btn" style={{ padding: '6px 12px', fontSize: 13 }} onClick={() => folderInput.current?.click()}>Ordner wählen</button>
            <input ref={folderInput} type="file" hidden multiple onChange={(e) => pick(e.target.files)}
              {...({ webkitdirectory: '', directory: '' } as Record<string, string>)} />
          </div>
        ) : (
          <label className="f">
            YouTube- oder Google-Drive-Link
            <input className="inp" value={drive} onChange={(e) => setDrive(e.target.value)} placeholder="https://www.youtube.com/watch?v=… oder https://drive.google.com/…" />
            <span className="note">YouTube: Wir holen den besten Moment aus dem Video – denselben Link noch einmal einfügen = der nächstbeste Clip. Bitte nur Videos, an denen du die Rechte hast. In Drive: Rechtsklick → Freigeben → „Jeder mit dem Link“. Bei einem Ordner schneiden wir alle Clips und Bilder darin zu einem Video – bitte nur das Rohmaterial für ein Video in den Ordner legen.</span>
          </label>
        )}

        <div>
          <p className="f" style={{ marginBottom: 6 }}>Schnitt-Stil</p>
          <div className="seg">
            {(Object.keys(STYLES) as StyleId[]).map((s) => (
              <label key={s}>
                <input type="radio" name="style" checked={style === s} onChange={() => setStyle(s)} />
                <b>{STYLES[s].name}{s === 'viral' ? ' 🔥' : ''}</b>
                <span>{STYLES[s].desc}</span>
                <em>{STYLES[s].credits} Credits pro Clip</em>
              </label>
            ))}
          </div>
        </div>

        <div>
          <p className="f" style={{ marginBottom: 6 }}>Untertitel</p>
          <div className="capgrid" role="radiogroup" aria-label="Untertitel-Stil">
            {CAPTIONS.map(([v, n]) => (
              <button key={v} type="button" role="radio" aria-checked={caption === v} className={`capbtn cs-${v}${caption === v ? ' on' : ''}`} onClick={() => setCaption(v)}>
                {n}
                <small>{CAPTION_HINT[v]}</small>
              </button>
            ))}
          </div>
        </div>
        <div className="row" style={{ alignItems: 'end' }}>
          <label className="f" style={{ width: 130 }}>
            Anzahl Clips
            <select className="inp" value={clips} onChange={(e) => setClips(+e.target.value)}>
              {Array.from({ length: MAX_CLIPS_PER_JOB }, (_, i) => <option key={i} value={i + 1}>{i + 1}</option>)}
            </select>
          </label>
          <p className="note" style={{ flex: 1, margin: 0 }}>Kurze Videos (unter 2½ Min.) werden zu einem fertigen Clip geschnitten. Ein Ordner wird zu einem Video – lade pro Ordner nur das Rohmaterial für ein Video hoch. Zu viel gebuchte Credits bekommst du automatisch zurück.</p>
        </div>
        <p className="f" style={{ marginBottom: -6 }}>Akzentfarbe</p>
        <div className="row" aria-label="Akzentfarbe">
          {COLORS.map((c) => (
            <button key={c} type="button" onClick={() => setColor(c)} aria-label={`Farbe ${c}`}
              style={{ width: 30, height: 30, borderRadius: '50%', background: c, border: color === c ? '3px solid #fff' : '2px solid rgba(255,255,255,.2)' }} />
          ))}
          <label title="Eigene Farbe (z. B. deine Markenfarbe)" className="color-own"
            style={{ border: COLORS.includes(color) ? '2px solid rgba(255,255,255,.2)' : '3px solid #fff', background: COLORS.includes(color) ? undefined : color }}>
            <input type="color" value={color} onChange={(e) => setColor(e.target.value.toUpperCase())} aria-label="Eigene Akzentfarbe wählen" />
          </label>
        </div>

        <div className="stack" style={{ gap: 10 }}>
          <p className="f" style={{ marginBottom: -4 }}>Extras <span className="muted" style={{ fontWeight: 400 }}>(freiwillig)</span></p>
          <label className="check">
            <input type="checkbox" checked={useLogo} onChange={(e) => setUseLogo(e.target.checked)} />
            <span>Mein Logo einbauen – oben links, schützt dein Video vor Kopieren</span>
          </label>
          {useLogo ? (
            <div className="row" style={{ alignItems: 'center', gap: 12, paddingLeft: 28 }}>
              {logoUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={logoUrl} alt="Dein Logo" style={{ width: 48, height: 48, objectFit: 'contain', borderRadius: 8, background: 'rgba(255,255,255,.06)' }} />
              ) : null}
              <input ref={logoInput} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={(e) => uploadLogo(e.target.files?.[0])} />
              <button type="button" className="btn" style={{ padding: '7px 14px', fontSize: 14 }} disabled={logoBusy} onClick={() => logoInput.current?.click()}>
                {logoBusy ? 'Lädt hoch …' : logoUrl ? 'Logo ändern' : 'Logo hochladen'}
              </button>
              <span className="note" style={{ margin: 0 }}>Am besten PNG mit transparentem Hintergrund.</span>
            </div>
          ) : null}
          <label className="check">
            <input type="checkbox" checked={music === 'auto'} onChange={(e) => setMusic(e.target.checked ? 'auto' : 'none')} />
            <span>Lizenzfreie Hintergrundmusik einbauen – passend zum Video, wird automatisch leiser, wenn jemand spricht</span>
          </label>
          <label className="f" style={{ marginTop: 4 }}>
            Namen &amp; Marken in deinen Videos <span className="muted" style={{ fontWeight: 400 }}>(für richtige Untertitel)</span>
            <input className="inp" value={brand} maxLength={300} onChange={(e) => setBrand(e.target.value)} onBlur={saveBrand}
              placeholder="z. B. Malo's, Corndogs, Smashburger, Mannheim" />
            <span className="note">Wird in deinem Konto gespeichert und gilt für alle Videos.</span>
          </label>
          <p className="note" style={{ margin: 0 }}>Außerdem schlagen wir dir zu jedem fertigen Video 3 aktuelle Trend-Songs vor, die du beim Posten direkt in TikTok hinzufügen kannst.</p>
        </div>

        <div className="prev-mobile"><CaptionPreview caption={caption} color={color} file={tab === 'upload' ? file : null} /></div>

        {up >= 0 ? <div><div className="bar"><i style={{ width: `${up}%` }} /></div><p className="note">Upload {up} %</p></div> : null}
        {err ? <p className="err">{err}</p> : null}
        <button className="btn btn-acc full" type="button" onClick={start} disabled={busy}>
          {busy ? (up >= 0 ? 'Wird hochgeladen …' : 'Einen Moment …') : `Schneiden – ${cost} Credits`}
        </button>
        <p className="note">Du hast {balance} Credits. Schlägt ein Schnitt fehl, bekommst du die Credits automatisch zurück.</p>
      </section>
      <aside className="studio-side">
        <CaptionPreview caption={caption} color={color} file={tab === 'upload' ? file : null} />
        <p className="note">So sehen Stil und Farbe im fertigen Video aus. Die Untertitel weichen automatisch aus, wenn dort ein Gesicht ist.</p>
      </aside>
    </div>

      <section className="card studio-jobs">
        <h2 style={{ font: '700 20px var(--display)', marginBottom: 14 }}>Deine Videos</h2>
        {jobs.length === 0 ? <p className="muted">Noch keine Videos. Lade oben dein erstes hoch.</p> : null}
        <div className="jobs">
          {jobs.map((j) => (
            <div className="job" key={j.id}>
              <div>
                <div className="t">{j.source_name || 'Video'}</div>
                <div className="m">{STYLES[j.style]?.name} · {j.clips} Clip{j.clips > 1 ? 's' : ''} · {new Date(j.created_at).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })}</div>
              </div>
              <span className={`badge ${j.status}`}>{STATUS[j.status]}</span>
              {j.status === 'processing' ? (
                <div className="job-live">
                  <div className="bar"><i style={{ width: `${Math.max(5, j.progress)}%` }} /></div>
                  <p className="m"><b>{Math.max(1, j.progress)} %</b> · {eta(j)}{j.stage ? <> · {j.stage}</> : null}</p>
                </div>
              ) : null}
              {j.status === 'queued' ? (
                <p className="m" style={{ gridColumn: '1 / -1' }}>
                  {online === false
                    ? 'Die Schnitt-Maschine ist gerade kurz nicht erreichbar. Dein Auftrag startet automatisch, sobald sie wieder da ist – sonst bekommst du nach spätestens 10 Minuten deine Credits automatisch zurück.'
                    : j.stage || 'Startet gleich – die Schnitt-Maschine holt sich deinen Auftrag.'}
                </p>
              ) : null}
              {j.status === 'failed' ? <p className="m" style={{ gridColumn: '1 / -1' }}>{j.error || 'Unbekannter Fehler'} – Credits wurden erstattet.</p> : null}
              {j.status === 'done' && j.outputs?.some((o) => o.songs?.length) ? (
                <div className="m" style={{ gridColumn: '1 / -1' }}>
                  <b>🎵 Song-Tipps für TikTok</b> (beim Posten unter „Sounds“ suchen und leise unter deinen Ton legen):
                  <ul style={{ margin: '4px 0 0 18px' }}>
                    {j.outputs.flatMap((o) => o.songs ?? []).slice(0, 3).map((x) => (
                      <li key={x.song + x.artist}>{x.song} – {x.artist}{x.warum ? <span className="muted"> · {x.warum}</span> : null}</li>
                    ))}
                  </ul>
                  <span className="note">Firmenkonten sehen in TikTok nur Songs aus der „Commercial Music Library“ – ist ein Tipp dort nicht zu finden, nimm einen ähnlichen von dort.</span>
                </div>
              ) : null}
              {j.status === 'done' ? (
                <div className="dl">
                  <button className="btn btn-acc" style={{ padding: '7px 14px', fontSize: 14 }} onClick={() => setOpen((o) => (o === j.id ? null : j.id))}>
                    {open === j.id ? 'Schließen' : `▶ Ansehen & posten`}
                  </button>
                  <button className="btn" style={{ padding: '7px 14px', fontSize: 14 }} onClick={() => download(j.id)}>⬇ {j.outputs?.length > 1 ? 'Alle herunterladen' : 'Herunterladen'}</button>
                </div>
              ) : null}
              {j.status === 'done' && open === j.id ? <Results jobId={j.id} /> : null}
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
