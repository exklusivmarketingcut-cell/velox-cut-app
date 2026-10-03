import type { EmailOtpType } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { createClient } from '@/lib/supabase/server';
import { safePath } from '@/lib/choice';

// Knopf auf /auth/bestaetigen (Link aus unseren E-Mails): Einmal-Code prüfen, Sitzung anlegen, weiterleiten
export async function POST(request: Request) {
  const url = new URL(request.url);
  const f = await request.formData();
  const tokenHash = String(f.get('token_hash') || '');
  const type = String(f.get('type') || '') as EmailOtpType;
  const ziel = safePath(String(f.get('weiter') || ''));
  if (tokenHash && type) {
    const supabase = await createClient();
    const { error } = await supabase.auth.verifyOtp({ type, token_hash: tokenHash });
    if (!error) return NextResponse.redirect(new URL(ziel, url.origin), 303);
  }
  return NextResponse.redirect(new URL('/login?fehler=link', url.origin), 303);
}

// Link aus Bestätigungs- oder Passwort-Mail (älteres Format mit ?code=): Code gegen Sitzung tauschen
export async function GET(request: Request) {
  const url = new URL(request.url);
  const code = url.searchParams.get('code');
  const ziel = safePath(url.searchParams.get('weiter'));
  if (code) {
    const supabase = await createClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) return NextResponse.redirect(new URL(ziel, url.origin));
  }
  return NextResponse.redirect(new URL('/login?fehler=link', url.origin));
}
