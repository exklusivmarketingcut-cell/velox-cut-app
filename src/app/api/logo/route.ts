import { NextResponse } from 'next/server';
import { z } from 'zod';
import { createAdmin, getUser } from '@/lib/supabase/server';
import { removeObject, uploadUrl, viewUrl } from '@/lib/r2';

// Kunden-Logo: liegt einmal im Konto (R2 logos/<user>/…) und kommt auf Wunsch oben links in jedes Video.
const MAX_LOGO_BYTES = 5 * 1024 * 1024;
const TYPES: Record<string, string> = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp' };

async function current(userId: string) {
  const { data } = await createAdmin().from('profiles').select('logo_path').eq('id', userId).single();
  return (data?.logo_path as string | null) ?? null;
}

/** Aktuelles Logo (Vorschau-Link) */
export async function GET() {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const path = await current(user.id);
  return NextResponse.json({ url: path ? await viewUrl(path).catch(() => null) : null });
}

/** Upload vorbereiten: signierter Link, der Browser lädt direkt in den Speicher */
export async function POST(request: Request) {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const b = z.object({ size: z.number().int().positive(), type: z.string() }).safeParse(await request.json().catch(() => null));
  if (!b.success || !TYPES[b.data.type]) return NextResponse.json({ error: 'Bitte ein Logo als PNG, JPG oder WebP hochladen.' }, { status: 400 });
  if (b.data.size > MAX_LOGO_BYTES) return NextResponse.json({ error: 'Das Logo ist größer als 5 MB.' }, { status: 400 });
  const path = `logos/${user.id}/${crypto.randomUUID()}.${TYPES[b.data.type]}`;
  const signedUrl = await uploadUrl(path).catch(() => null);
  if (!signedUrl) return NextResponse.json({ error: 'Upload konnte nicht vorbereitet werden.' }, { status: 500 });
  return NextResponse.json({ path, signedUrl });
}

/** Hochgeladenes Logo im Konto speichern (altes wird gelöscht) */
export async function PUT(request: Request) {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const b = z.object({ path: z.string().max(200) }).safeParse(await request.json().catch(() => null));
  if (!b.success || !b.data.path.startsWith(`logos/${user.id}/`)) return NextResponse.json({ error: 'Logo fehlt.' }, { status: 400 });
  const old = await current(user.id);
  const { error } = await createAdmin().from('profiles').update({ logo_path: b.data.path }).eq('id', user.id);
  if (error) return NextResponse.json({ error: 'Logo konnte nicht gespeichert werden.' }, { status: 500 });
  if (old && old !== b.data.path) await removeObject(old);
  return NextResponse.json({ url: await viewUrl(b.data.path).catch(() => null) });
}

/** Logo entfernen */
export async function DELETE() {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const old = await current(user.id);
  await createAdmin().from('profiles').update({ logo_path: null }).eq('id', user.id);
  if (old) await removeObject(old);
  return NextResponse.json({ url: null });
}
