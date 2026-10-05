'use client';
import { useEffect, useState } from 'react';

type File = { title: string; url: string | null; view: string | null; seconds: number | null; post: { text: string; hashtags: string[] } | null };

/** Fertige Clips direkt im Studio: abspielen, einzeln herunterladen, Beschreibungstext + Hashtags kopieren. */
export default function Results({ jobId }: { jobId: string }) {
  const [files, setFiles] = useState<File[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [copied, setCopied] = useState(-1);

  useEffect(() => {
    let live = true;
    fetch(`/api/jobs/${jobId}/files`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d) => live && setFiles(d.files ?? []))
      .catch(() => live && setFailed(true));
    return () => { live = false; };
  }, [jobId]);

  async function copy(i: number, f: File) {
    if (!f.post) return;
    const text = `${f.post.text}\n\n${f.post.hashtags.join(' ')}`;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(i);
      setTimeout(() => setCopied((c) => (c === i ? -1 : c)), 2000);
    } catch {
      window.prompt('Zum Kopieren markieren:', text);
    }
  }

  if (failed) return <p className="m res-full">Die Clips konnten gerade nicht geladen werden – bitte Seite neu laden.</p>;
  if (!files) return <p className="m res-full">Lädt deine Clips …</p>;
  if (!files.length) return <p className="m res-full">Keine Clips mehr vorhanden (fertige Clips werden nach 90 Tagen gelöscht).</p>;
  return (
    <div className="res res-full">
      {files.map((f, i) => (
        <div className="res-clip" key={i}>
          {f.view ? <video src={f.view} controls playsInline preload="metadata" /> : <div className="res-novid">Vorschau nicht verfügbar</div>}
          <div className="res-body">
            <div className="t">{f.title}{f.seconds ? <span className="muted"> · {f.seconds} Sek.</span> : null}</div>
            {f.url ? <a className="btn res-dl" href={f.url}>⬇ Herunterladen</a> : null}
            {f.post ? (
              <div className="res-post">
                <div className="res-post-h">
                  <b>Beschreibung zum Posten</b>
                  <button type="button" className="btn res-copy" onClick={() => copy(i, f)}>{copied === i ? '✓ Kopiert' : 'Kopieren'}</button>
                </div>
                <p>{f.post.text}</p>
                <p className="res-tags">{f.post.hashtags.join(' ')}</p>
              </div>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}
