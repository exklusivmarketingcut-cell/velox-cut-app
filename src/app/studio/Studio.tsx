'use client';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import CaptionPreview from './CaptionPreview';
import { jobCost, MAX_CLIPS_PER_JOB, MAX_UPLOAD_BYTES, STYLES, type StyleId } from '@/lib/pricing';

type Job = {
  id: string; status: 'queued' | 'processing' | 'done' | 'failed'; style: StyleId; clips: number; source_name: string | null;
  source_kind: string; credits_charged: number; progress: number; error: string | null; created_at: string; outputs: unknown[];
};

const CAPTIONS = [
  ['rise', 'Rise'], ['hormozi', 'Pop'], ['clean', 'Karaoke'], ['neon', 'Neon'], ['comic', 'Comic'], ['serif', 'Serif'],
] as const;
const CAPTION_HINT: Record<string, string> = {
  rise: 'weich, modern', hormozi: 'groß, knallig', clean: 'Wort für Wort', neon: 'leuchtend', comic: 'verspielt', serif: 'elegant',
};
const COLORS = ['#F6E27F', '#FF3D7F', '#4F9EFF', '#3DFF9A', '#FF7A3D', '#FFFFFF'];
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

/** Link, den jemand auf der Startseite eingefügt hat (wird dort in sessionStorage gemerkt) */
const readHeroLink = () => {
  try { return sessionStorage.getItem('velox_link'); } catch { return null; }
};
const noSubscribe = () => () => {};
const DRIVE_RE = /^https:\/\/(drive|docs)\.google\.com\//;

export default function Studio({ credits, initialJobs }: { credits: number; initialJobs: Job[] }) {
  const [tab, setTab] = useState<'upload' | 'drive'>('upload');
  const [file, setFile] = useState<File | null>(null);
  const [drive, setDrive] = useState('');
  const [style, setStyle] = useState<StyleId>('clean');
  const [caption, setCaption] = useState('rise');
  const [color, setColor] = useState(COLORS[0]);
  const [clips, setClips] = useState(1);
  const [up, setUp] = useState(-1);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<React.ReactNode>('');
  const [jobs, setJobs] = useState<Job[]>(initialJobs);
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
  const router = useRouter();
  const failedSeen = useRef(new Set(initialJobs.filter((j) => j.status === 'failed').map((j) => j.id)));

  const cost = jobCost(style, clips);
  const reload = useCallback(async () => {
    const r = await fetch('/api/jobs', { cache: 'no-store' });
    if (!r.ok) return;
    const next: Job[] = (await r.json()).jobs;
    setJobs(next);
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

  function pick(f?: File | null) {
    setErr('');
    if (!f) return;
    if (!f.type.startsWith('video/') && !f.type.startsWith('audio/')) return setErr('Bitte wähle ein Video aus.');
    if (f.size > MAX_UPLOAD_BYTES) return setErr('Die Datei ist größer als 2 GB.');
    setFile(f);
  }

  async function start() {
    setErr('');
    if (cost > balance)
      return setErr(<>Dafür brauchst du {cost} Credits, du hast {balance}. <Link href="/konto#nachkauf">Credits nachkaufen →</Link></>);
    let sourcePath: string | undefined;
    let sourceName: string | undefined;
    setBusy(true);
    try {
      if (tab === 'upload') {
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
      } else if (!/^https:\/\/(drive|docs)\.google\.com\//.test(drive.trim())) {
        throw new Error('Bitte füge einen Google-Drive-Link ein (Freigabe: „Jeder mit dem Link“).');
      } else sourceName = 'Google Drive';
      const r = await fetch('/api/jobs', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sourceKind: tab, sourcePath, driveUrl: tab === 'drive' ? drive.trim() : undefined, sourceName, style, captionStyle: caption, captionColor: color, clips }),
      });
      const d = await r.json();
      if (!r.ok) {
        if (d.error === 'credits') throw new Error(`Dafür brauchst du ${d.need} Credits, du hast ${d.have}.`);
        throw new Error(d.error || 'Das hat nicht geklappt.');
      }
      setBalance((b) => b - d.cost);
      router.refresh(); // Credit-Anzeige oben aktualisieren
      setFile(null);
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
              <span>Diesen Link können wir noch nicht direkt öffnen. Lade das Video als Datei hoch oder teile es über Google Drive.</span>
              <button type="button" className="btn" onClick={() => dropHeroLink(false)}>Okay</button>
            </div>
          )
        ) : null}
        <div className="tabs" role="tablist">
          <button className={tab === 'upload' ? 'on' : ''} onClick={() => setTab('upload')} type="button">📁 Datei hochladen</button>
          <button className={tab === 'drive' ? 'on' : ''} onClick={() => setTab('drive')} type="button">Google Drive</button>
        </div>
        {tab === 'upload' ? (
          <div
            className={`drop${over ? ' over' : ''}`}
            onClick={() => input.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files[0]); }}
          >
            <input ref={input} type="file" accept="video/*" hidden onChange={(e) => pick(e.target.files?.[0])} />
            <b>{file ? `🎬 ${file.name}` : 'Video hierher ziehen oder klicken'}</b>
            <small>{file ? `${(file.size / 1024 / 1024).toFixed(0)} MB` : 'MP4, MOV … bis 2 GB'}</small>
          </div>
        ) : (
          <label className="f">
            Google-Drive-Link
            <input className="inp" value={drive} onChange={(e) => setDrive(e.target.value)} placeholder="https://drive.google.com/file/d/…" />
            <span className="note">In Drive: Rechtsklick → Freigeben → „Jeder mit dem Link“.</span>
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
          <p className="note" style={{ flex: 1, margin: 0 }}>Kurze Videos (unter 2½ Min.) werden zu einem fertigen Clip geschnitten – zu viel gebuchte Credits bekommst du automatisch zurück.</p>
        </div>
        <p className="f" style={{ marginBottom: -6 }}>Akzentfarbe</p>
        <div className="row" aria-label="Akzentfarbe">
          {COLORS.map((c) => (
            <button key={c} type="button" onClick={() => setColor(c)} aria-label={`Farbe ${c}`}
              style={{ width: 30, height: 30, borderRadius: '50%', background: c, border: color === c ? '3px solid #fff' : '2px solid rgba(255,255,255,.2)' }} />
          ))}
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
              {j.status === 'processing' ? <div style={{ gridColumn: '1 / -1' }} className="bar"><i style={{ width: `${Math.max(5, j.progress)}%` }} /></div> : null}
              {j.status === 'failed' ? <p className="m" style={{ gridColumn: '1 / -1' }}>{j.error || 'Unbekannter Fehler'} – Credits wurden erstattet.</p> : null}
              {j.status === 'done' ? <div className="dl"><button className="btn" style={{ padding: '7px 14px', fontSize: 14 }} onClick={() => download(j.id)}>⬇ Herunterladen</button></div> : null}
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
