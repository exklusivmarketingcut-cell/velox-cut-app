import { NextResponse } from 'next/server';
import { z } from 'zod';
import { getUser } from '@/lib/supabase/server';
import { uploadUrl } from '@/lib/r2';
import { MAX_UPLOAD_BYTES } from '@/lib/pricing';

const Body = z.object({ name: z.string().min(1).max(200), size: z.number().int().positive(), type: z.string().max(100) });

// Signierter Upload-Link: der Browser lädt die Datei direkt in den Speicher (Cloudflare R2), nicht über die App
export async function POST(request: Request) {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: 'Ungültige Datei.' }, { status: 400 });
  const { name, size, type } = parsed.data;
  if (!type.startsWith('video/') && !type.startsWith('audio/')) return NextResponse.json({ error: 'Bitte lade ein Video hoch.' }, { status: 400 });
  if (size > MAX_UPLOAD_BYTES) return NextResponse.json({ error: 'Die Datei ist größer als 2 GB.' }, { status: 400 });
  const safe = name.normalize('NFKD').replace(/[^\w.\-]+/g, '_').slice(-80);
  const path = `${user.id}/${crypto.randomUUID()}-${safe}`;
  const signedUrl = await uploadUrl(`uploads/${path}`).catch(() => null);
  if (!signedUrl) return NextResponse.json({ error: 'Upload konnte nicht vorbereitet werden.' }, { status: 500 });
  return NextResponse.json({ path, signedUrl });
}
