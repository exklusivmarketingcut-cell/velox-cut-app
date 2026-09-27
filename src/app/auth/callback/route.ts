import { NextResponse } from 'next/server';
import { createClient } from '@/lib/supabase/server';
import { safePath } from '@/lib/choice';

// Link aus Bestätigungs- oder Passwort-Mail: Code gegen Sitzung tauschen
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
