import { NextResponse } from 'next/server';
import { createAdmin, getUser } from '@/lib/supabase/server';
import { downloadUrl } from '@/lib/r2';

// Download-Links für fertige Clips (1 Stunde gültig)
export async function GET(_req: Request, ctx: RouteContext<'/api/jobs/[id]/files'>) {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const { id } = await ctx.params;
  const admin = createAdmin();
  const { data: job } = await admin.from('jobs').select('user_id, outputs, status').eq('id', id).single();
  if (!job || job.user_id !== user.id) return NextResponse.json({ error: 'nicht gefunden' }, { status: 404 });
  const outs = (job.outputs as { path: string; title?: string }[]) ?? [];
  const files = await Promise.all(
    outs.map(async (o, i) => {
      const url = await downloadUrl(`outputs/${o.path}`, `velox-clip-${i + 1}.mp4`).catch(() => null);
      return { title: o.title ?? `Clip ${i + 1}`, url };
    }),
  );
  return NextResponse.json({ files });
}
