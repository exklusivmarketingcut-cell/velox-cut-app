'use client';
import { useEffect, useMemo, useState } from 'react';

// Beispielsatz, Wort für Wort – * = hervorgehobenes Wort (wie im fertigen Video)
const SAMPLE = ['So', 'sehen', 'deine', '*Untertitel', 'im', 'fertigen', '*Video', 'aus.'];
// Wörter pro Einblendung je Stil (identisch mit worker/pipeline.py)
const PER: Record<string, number> = { rise: 4, clean: 4, hormozi: 3, neon: 3, comic: 2, serif: 4 };

/** Live-Vorschau: gewählter Untertitel-Stil + Farbe auf dem eigenen Video (sobald gewählt) oder einem Beispielbild */
export default function CaptionPreview({ caption, color, file }: { caption: string; color: string; file: File | null }) {
  const [i, setI] = useState(0);
  const src = useMemo(() => (file && file.type.startsWith('video/') ? URL.createObjectURL(file) : null), [file]);
  useEffect(() => () => { if (src) URL.revokeObjectURL(src); }, [src]);
  useEffect(() => {
    const t = setInterval(() => setI((x) => (x + 1) % (SAMPLE.length + 3)), 380);
    return () => clearInterval(t);
  }, []);

  const n = PER[caption] ?? 4;
  const cur = Math.min(i, SAMPLE.length - 1);
  const start = Math.floor(cur / n) * n;
  const group = SAMPLE.slice(start, start + n);

  return (
    <div className="cprev" style={{ ['--cap' as string]: color }}>
      {src ? (
        <video src={src} muted loop playsInline autoPlay aria-hidden="true" />
      ) : (
        // eslint-disable-next-line @next/next/no-img-element
        <img src="/assets/demo/studio_raw.jpg" alt="" aria-hidden="true" />
      )}
      <div className={`cprev-cap cs-${caption}`} key={`${caption}-${start}`}>
        {group.map((w, k) => {
          const idx = start + k;
          const shown = idx <= cur;
          const on = idx === cur && i < SAMPLE.length;
          const emph = w.startsWith('*');
          return (
            <span key={idx} className={`w${shown ? ' in' : ''}${on ? ' cur' : ''}${emph ? ' e' : ''}`}>
              {w.replace('*', '')}
            </span>
          );
        })}
      </div>
      <span className="cprev-tag">{src ? 'Vorschau mit deinem Video' : 'Vorschau'}</span>
    </div>
  );
}
