import { NextResponse } from 'next/server';
import { z } from 'zod';
import { getUser } from '@/lib/supabase/server';
import { uploadUrl } from '@/lib/r2';
import { MAX_UPLOAD_BYTES } from '@/lib/pricing';

const Body = z.object({
  name: z.string().min(1).max(200), size: z.number().int().positive(), type: z.string().max(100),
  batch: z.string().uuid().optional(), // mehrere Dateien (Ordner) → gemeinsamer Stapel-Ordner
});

// Signierter Upload-Link: der Browser lädt die Datei direkt in den Speicher (Cloudflare R2), nicht über die App
export async function POST(request: Request) {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: 'Ungültige Datei.' }, { status: 400 });
  const { name, size, type, batch } = parsed.data;
  const image = batch && /^image\/(png|jpe?g|webp|heic|heif)$/.test(type);
  if (!type.startsWith('video/') && !type.startsWith('audio/') && !image) return NextResponse.json({ error: 'Bitte lade ein Video hoch.' }, { status: 400 });
  if (size > MAX_UPLOAD_BYTES) return NextResponse.json({ error: 'Die Datei ist größer als 4 GB – bitte als Google-Drive-Link einfügen.' }, { status: 400 });
  const safe = name.normalize('NFKD').replace(/[^\w.\-]+/g, '_').slice(-80);
  const path = batch ? `${user.id}/${batch}/${crypto.randomUUID().slice(0, 8)}-${safe}` : `${user.id}/${crypto.randomUUID()}-${safe}`;
  const signedUrl = await uploadUrl(`uploads/${path}`).catch(() => null);
  if (!signedUrl) return NextResponse.json({ error: 'Upload konnte nicht vorbereitet werden.' }, { status: 500 });
  return NextResponse.json({ path, signedUrl });
}
