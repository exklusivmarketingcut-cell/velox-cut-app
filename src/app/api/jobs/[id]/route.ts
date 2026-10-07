import { NextResponse } from 'next/server';
import { createAdmin, getUser } from '@/lib/supabase/server';
import { removeObject } from '@/lib/r2';

// Fertiges (oder fehlgeschlagenes) Video aus „Deine Videos“ löschen: Eintrag und Clip-Dateien.
// Laufende und wartende Aufträge lassen sich nicht löschen (sonst gingen Credits ohne Ergebnis verloren).
export async function DELETE(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const { id } = await ctx.params;
  const admin = createAdmin();
  const { data: job } = await admin.from('jobs').select('user_id, outputs, status').eq('id', id).single();
  if (!job || job.user_id !== user.id) return NextResponse.json({ error: 'nicht gefunden' }, { status: 404 });
  if (job.status !== 'done' && job.status !== 'failed') return NextResponse.json({ error: 'Dieses Video wird gerade noch geschnitten.' }, { status: 409 });
  const { error } = await admin.from('jobs').delete().eq('id', id).eq('user_id', user.id);
  if (error) return NextResponse.json({ error: 'Löschen hat nicht geklappt.' }, { status: 500 });
  for (const o of (job.outputs as { path: string }[] | null) ?? []) await removeObject(`outputs/${o.path}`).catch(() => null);
  return NextResponse.json({ ok: true });
}
