import { NextResponse } from 'next/server';
import { createAdmin, getUser } from '@/lib/supabase/server';
import { CODE_ERR, cardFingerprint, findCode } from '@/lib/codes';

// Credit-Gutschein einlösen (Gratis-Monat läuft über /api/stripe/subscribe mit code)
export async function POST(request: Request) {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const { code: raw } = await request.json().catch(() => ({}));
  const { code, error } = await findCode(raw);
  if (!code) return NextResponse.json({ error }, { status: 400 });
  if (code.art !== 'credits') return NextResponse.json({ error: 'Mit diesem Code startest du einen Gratis-Monat – wähle dafür unten das Paket.' }, { status: 400 });
  const admin = createAdmin();
  const { data: p } = await admin.from('profiles').select('card_on_file, default_payment_method').eq('id', user.id).single();
  if (!p?.card_on_file) return NextResponse.json({ error: 'Bitte zuerst eine Karte hinterlegen – abgebucht wird nichts.' }, { status: 400 });
  const { data: res, error: e } = await admin.rpc('redeem_code', { p_code: code.code, p_user: user.id, p_card: await cardFingerprint(p.default_payment_method), p_art: 'credits' });
  if (e) return NextResponse.json({ error: 'Das hat nicht geklappt. Bitte versuch es noch einmal.' }, { status: 500 });
  if (res !== 'ok') return NextResponse.json({ error: CODE_ERR[res as string] ?? 'Code nicht einlösbar.' }, { status: 400 });
  return NextResponse.json({ ok: true, credits: code.credits });
}
