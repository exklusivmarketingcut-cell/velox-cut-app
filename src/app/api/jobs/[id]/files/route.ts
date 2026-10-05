import { NextResponse } from 'next/server';
import { createAdmin, getUser } from '@/lib/supabase/server';
import { downloadUrl, viewUrl } from '@/lib/r2';

// Links für fertige Clips (1 Stunde gültig): url = Download (speichert als Datei), view = Abspielen im Studio
export async function GET(_req: Request, ctx: RouteContext<'/api/jobs/[id]/files'>) {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const { id } = await ctx.params;
  const admin = createAdmin();
  const { data: job } = await admin.from('jobs').select('user_id, outputs, status').eq('id', id).single();
  if (!job || job.user_id !== user.id) return NextResponse.json({ error: 'nicht gefunden' }, { status: 404 });
  const outs = (job.outputs as { path: string; title?: string; seconds?: number; post?: { text: string; hashtags: string[] } | null }[]) ?? [];
  const files = await Promise.all(
    outs.map(async (o, i) => {
      const [url, view] = await Promise.all([
        downloadUrl(`outputs/${o.path}`, `velox-clip-${i + 1}.mp4`).catch(() => null),
        viewUrl(`outputs/${o.path}`).catch(() => null),
      ]);
      return { title: o.title ?? `Clip ${i + 1}`, url, view, seconds: o.seconds ?? null, post: o.post ?? null };
    }),
  );
  return NextResponse.json({ files });
}
