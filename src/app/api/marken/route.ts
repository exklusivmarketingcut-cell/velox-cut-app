import { NextResponse } from 'next/server';
import { z } from 'zod';
import { createAdmin, getUser } from '@/lib/supabase/server';

// Namen & Marken des Kunden (für richtige Untertitel) – einmal im Konto gespeichert, gilt für alle Videos.
export async function GET() {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const { data } = await createAdmin().from('profiles').select('brand_words').eq('id', user.id).single();
  return NextResponse.json({ words: (data?.brand_words as string | null) ?? '' });
}

export async function PUT(request: Request) {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const b = z.object({ words: z.string().max(300) }).safeParse(await request.json().catch(() => null));
  if (!b.success) return NextResponse.json({ error: 'Bitte höchstens 300 Zeichen.' }, { status: 400 });
  const words = b.data.words.replace(/\s+/g, ' ').trim();
  const { error } = await createAdmin().from('profiles').update({ brand_words: words || null }).eq('id', user.id);
  if (error) return NextResponse.json({ error: 'Konnte nicht gespeichert werden.' }, { status: 500 });
  return NextResponse.json({ words });
}
