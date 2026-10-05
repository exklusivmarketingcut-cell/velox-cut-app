import { timingSafeEqual } from 'node:crypto';
import { NextResponse } from 'next/server';
import { z } from 'zod';
import { createAdmin } from '@/lib/supabase/server';
import { OWNER, SUPPORT, sendMail } from '@/lib/email';

// Nur für die Schnitt-Maschine: sie meldet sich mit dem service_role-Schlüssel (hat sie ohnehin) und lässt die App
// E-Mails verschicken – so braucht die Maschine keinen eigenen Resend-Schlüssel.
//   fertig  → Kunde: „Dein Video ist fertig“ mit Link ins Studio
//   warnung → Betreiber: z. B. „KI-Guthaben leer – Aufträge warten“
const Body = z.discriminatedUnion('art', [
  z.object({ art: z.literal('fertig'), job: z.uuid() }),
  z.object({ art: z.literal('warnung'), betreff: z.string().min(1).max(150), text: z.string().min(1).max(4000) }),
]);

function authorized(request: Request) {
  const want = Buffer.from(`Bearer ${process.env.SUPABASE_SERVICE_ROLE_KEY ?? ''}`);
  const got = Buffer.from(request.headers.get('authorization') ?? '');
  return want.length > 20 && got.length === want.length && timingSafeEqual(got, want);
}

export async function POST(request: Request) {
  if (!authorized(request)) return NextResponse.json({ error: 'nicht erlaubt' }, { status: 401 });
  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: 'ungültig' }, { status: 400 });
  const b = parsed.data;

  if (b.art === 'warnung') return NextResponse.json({ ok: await sendMail(OWNER, `⚠️ Velox Cut: ${b.betreff}`, b.text) });

  const admin = createAdmin();
  const { data: job } = await admin.from('jobs').select('user_id, status, outputs, source_name').eq('id', b.job).single();
  if (!job || job.status !== 'done') return NextResponse.json({ error: 'Auftrag nicht fertig' }, { status: 409 });
  const { data: p } = await admin.from('profiles').select('email').eq('id', job.user_id).single();
  if (!p?.email) return NextResponse.json({ error: 'keine E-Mail' }, { status: 404 });
  const outs = (job.outputs ?? []) as { title?: string; seconds?: number }[];
  const n = outs.length;
  const liste = outs.map((o, i) => `  ${i + 1}. ${o.title || `Clip ${i + 1}`}${o.seconds ? ` (${o.seconds} Sek.)` : ''}`).join('\n');
  const text = [
    'Hi,',
    '',
    `${n === 1 ? 'dein Video ist' : `deine ${n} Videos sind`} fertig geschnitten${job.source_name ? ` (aus „${job.source_name}“)` : ''}:`,
    '',
    liste,
    '',
    'Ansehen und herunterladen: https://velox-cut.de/studio',
    '',
    'Dort findest du auch einen fertigen Beschreibungstext mit Hashtags und 3 Trend-Songs, die du beim Posten in TikTok hinzufügen kannst.',
    'Die Clips bleiben 90 Tage gespeichert.',
    '',
    'Viel Erfolg beim Posten!',
    'Dein Velox-Cut-Team',
  ].join('\n');
  const ok = await sendMail(p.email, n === 1 ? `🎬 Dein Video ist fertig: ${outs[0]?.title || 'Clip 1'}` : `🎬 Deine ${n} Videos sind fertig`, text, SUPPORT);
  return NextResponse.json({ ok });
}
