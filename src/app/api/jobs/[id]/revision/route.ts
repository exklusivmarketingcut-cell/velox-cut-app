import { NextResponse } from 'next/server';
import { z } from 'zod';
import { createAdmin, getUser } from '@/lib/supabase/server';
import { REVISION_AKTIV, revisionCost, type StyleId } from '@/lib/pricing';

// Änderungswunsch zu einem fertigen Video (Inhaber 11.10.): Der Kunde schreibt, was anders sein soll; das Video wird gezielt
// verbessert. Die erste Änderung ist gratis, jede weitere kostet ein Viertel des Stil-Preises. Das Rohmaterial liegt dafür
// 24 Stunden nach dem Schnitt noch bereit (keep_until) – danach ist keine Änderung mehr möglich.
const Body = z.object({ note: z.string().trim().min(5).max(600) });

export async function POST(req: Request, ctx: { params: Promise<{ id: string }> }) {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  if (!REVISION_AKTIV) return NextResponse.json({ error: 'Änderungswünsche kommen bald.' }, { status: 400 });
  const parsed = Body.safeParse(await req.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: 'Bitte beschreibe kurz, was geändert werden soll.' }, { status: 400 });
  const { id } = await ctx.params;
  const admin = createAdmin();
  const { data: job } = await admin.from('jobs')
    .select('user_id, status, style, clips, source_kind, source_path, source_paths, source_url, revisions, keep_until').eq('id', id).single();
  if (!job || job.user_id !== user.id) return NextResponse.json({ error: 'nicht gefunden' }, { status: 404 });
  if (job.status !== 'done') return NextResponse.json({ error: 'Dieses Video ist noch nicht fertig.' }, { status: 409 });
  const hatRoh = job.source_kind === 'drive' ? !!job.source_url : !!job.source_path || ((job.source_paths as string[] | null)?.length ?? 0) > 0;
  if (!hatRoh || (job.keep_until && new Date(job.keep_until) < new Date()))
    return NextResponse.json({ error: 'Änderungen sind bis 24 Stunden nach dem Schnitt möglich – das Rohmaterial ist inzwischen gelöscht.' }, { status: 410 });

  const nr = (job.revisions ?? 0) + 1;
  const cost = revisionCost(job.style as StyleId, job.clips, nr);
  const ref = `${id}-rev${nr}`;
  if (cost > 0) {
    const { data: ok, error: spendErr } = await admin.rpc('spend_credits', { p_user: user.id, p_amount: cost, p_reason: 'job', p_ref: ref });
    if (spendErr) return NextResponse.json({ error: 'Credits konnten nicht gebucht werden.' }, { status: 500 });
    if (!ok) return NextResponse.json({ error: 'credits', need: cost }, { status: 402 });
  }
  // zurück in die Schlange – nur wenn der Auftrag noch „fertig“ ist (kein doppelter Klick)
  const { data: upd, error } = await admin.from('jobs')
    .update({ status: 'queued', progress: 0, stage: null, error: null, worker: null, started_at: null, revision_note: parsed.data.note, revisions: nr, revision_cost: cost })
    .eq('id', id).eq('status', 'done').select('id');
  if (error || !upd?.length) {
    if (cost > 0) await admin.rpc('refund_credits', { p_user: user.id, p_amount: cost, p_ref: ref });
    return NextResponse.json({ error: 'Der Änderungswunsch konnte nicht angelegt werden.' }, { status: 500 });
  }
  return NextResponse.json({ ok: true, cost, nr });
}
