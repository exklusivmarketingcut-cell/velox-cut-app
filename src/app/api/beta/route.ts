import { NextResponse } from 'next/server';
import { z } from 'zod';
import { createAdmin } from '@/lib/supabase/server';
import { OWNER, sendMail } from '@/lib/email';

const Body = z.object({
  topic: z.string().max(40).optional(),
  name: z.string().trim().min(1).max(120),
  email: z.email().max(200),
  msg: z.string().max(4000).optional(),
  'bot-field': z.string().max(0).optional(), // Honeypot: muss leer sein
});

// Kontakt-/Beta-Formular der Website
export async function POST(request: Request) {
  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: 'Bitte Name und gültige E-Mail angeben.' }, { status: 400 });
  const b = parsed.data;
  const { error } = await createAdmin().from('beta_requests').insert({ topic: b.topic, name: b.name, email: b.email, message: b.msg });
  if (error) return NextResponse.json({ error: 'Speichern fehlgeschlagen.' }, { status: 500 });
  await sendMail(OWNER, `Neue Anfrage: ${b.name}`, `Thema: ${b.topic ?? '-'}\nName: ${b.name}\nE-Mail: ${b.email}\n\n${b.msg ?? ''}`, b.email);
  return NextResponse.json({ ok: true });
}
