import { NextResponse } from 'next/server';
import { z } from 'zod';
import { createAdmin, createClient, getUser } from '@/lib/supabase/server';
import { jobCost, MAX_CLIPS_PER_JOB, PLANS, PREMIUM_AKTIV, type PlanId } from '@/lib/pricing';

const DRIVE = /^https:\/\/((drive|docs)\.google\.com|(www\.|m\.)?youtube\.com|youtu\.be)\//; // Google Drive oder YouTube
const Body = z.object({
  sourceKind: z.enum(['upload', 'drive', 'folder']),
  sourcePath: z.string().max(400).optional(),
  sourcePaths: z.array(z.string().max(400)).max(300).optional(), // Ordner: alle Videos + Bilder
  driveUrl: z.string().max(600).optional(),
  sourceName: z.string().max(200).optional(),
  style: z.enum(['clean', 'viral', 'premium']),
  captionStyle: z.enum(['rise', 'hormozi', 'clean', 'neon', 'comic', 'serif', 'bold', 'box', 'impact', 'mini', 'wide']),
  captionColor: z.string().regex(/^#[0-9a-fA-F]{6}$/),
  clips: z.number().int().min(1).max(MAX_CLIPS_PER_JOB),
  useLogo: z.boolean().optional(),
  music: z.enum(['none', 'auto']).optional(),
});

export async function GET() {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const supabase = await createClient();
  const { data } = await supabase.from('jobs').select('*').order('created_at', { ascending: false }).limit(50);
  // Ist die Schnitt-Maschine erreichbar? (null = unbekannt, z. B. Wächter noch nicht eingerichtet)
  const { data: online, error } = await supabase.rpc('worker_online');
  return NextResponse.json({ jobs: data ?? [], online: error ? null : online });
}

// Neuer Schnitt-Auftrag: Credits zuerst abbuchen (atomar), dann Auftrag in die Warteschlange
export async function POST(request: Request) {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: 'Angaben unvollständig.' }, { status: 400 });
  const b = parsed.data;
  if (b.style === 'premium' && !PREMIUM_AKTIV) return NextResponse.json({ error: 'Premium kommt bald.' }, { status: 400 });
  if (b.sourceKind === 'upload' && !b.sourcePath?.startsWith(`${user.id}/`)) return NextResponse.json({ error: 'Datei fehlt.' }, { status: 400 });
  if (b.sourceKind === 'drive' && !DRIVE.test(b.driveUrl ?? '')) return NextResponse.json({ error: 'Bitte einen Google-Drive- oder YouTube-Link einfügen.' }, { status: 400 });
  if (b.sourceKind === 'folder' && (!b.sourcePaths?.length || b.sourcePaths.some((x) => !x.startsWith(`${user.id}/`))))
    return NextResponse.json({ error: 'Dateien fehlen.' }, { status: 400 });

  const admin = createAdmin();
  const { data: pre } = await admin.from('profiles').select('plan').eq('id', user.id).single();
  if (pre) await admin.rpc('refresh_credits', { p_user: user.id, p_plan_amount: PLANS[pre.plan as PlanId].credits });
  const { data: p } = await admin.from('profiles').select('card_on_file, plan_credits, topup_credits').eq('id', user.id).single();
  if (!p?.card_on_file) return NextResponse.json({ error: 'karte' }, { status: 402 });
  if (b.useLogo) {
    const { data: lp } = await admin.from('profiles').select('logo_path').eq('id', user.id).single();
    if (!lp?.logo_path) return NextResponse.json({ error: 'Bitte lade zuerst dein Logo hoch.' }, { status: 400 });
  }

  const cost = jobCost(b.style, b.clips);
  const id = crypto.randomUUID();
  const { data: ok, error: spendErr } = await admin.rpc('spend_credits', { p_user: user.id, p_amount: cost, p_reason: 'job', p_ref: id });
  if (spendErr) return NextResponse.json({ error: 'Credits konnten nicht gebucht werden.' }, { status: 500 });
  if (!ok) return NextResponse.json({ error: 'credits', need: cost, have: p.plan_credits + p.topup_credits }, { status: 402 });

  const { error } = await admin.from('jobs').insert({
    id, user_id: user.id, style: b.style, caption_style: b.captionStyle, caption_color: b.captionColor, clips: b.clips,
    source_kind: b.sourceKind, source_path: b.sourceKind === 'upload' ? b.sourcePath : null,
    source_url: b.sourceKind === 'drive' ? b.driveUrl : null, source_name: b.sourceName ?? null, credits_charged: cost,
    // Extras nur mitschicken, wenn gewählt (so funktioniert der Auftrag auch, falls die Spalten noch fehlen)
    ...(b.sourceKind === 'folder' ? { source_paths: b.sourcePaths } : {}),
    ...(b.useLogo ? { use_logo: true } : {}),
    ...(b.music === 'auto' ? { music: 'auto' } : {}),
  });
  if (error) {
    // Auftrag ließ sich nicht anlegen → Credits sofort zurück
    await admin.rpc('refund_credits', { p_user: user.id, p_amount: cost, p_ref: id });
    return NextResponse.json({ error: 'Auftrag konnte nicht angelegt werden. Credits wurden erstattet.' }, { status: 500 });
  }
  return NextResponse.json({ id, cost });
}
