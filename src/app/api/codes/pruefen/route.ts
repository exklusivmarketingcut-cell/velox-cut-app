import { NextResponse } from 'next/server';
import { createAdmin, getUser } from '@/lib/supabase/server';
import { CODE_ERR, describeCode, findCode } from '@/lib/codes';
import { PLANS } from '@/lib/pricing';

// Code prüfen (noch nicht einlösen): was bringt er?
export async function POST(request: Request) {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const { code: raw } = await request.json().catch(() => ({}));
  const { code, error } = await findCode(raw);
  if (!code) return NextResponse.json({ error }, { status: 400 });
  const { count } = await createAdmin().from('code_uses').select('code', { count: 'exact', head: true }).eq('code', code.code).eq('user_id', user.id);
  if (count) return NextResponse.json({ error: CODE_ERR.schon_eingeloest }, { status: 400 });
  return NextResponse.json({
    code: code.code, art: code.art, credits: code.credits, plan: code.plan,
    planName: code.plan ? PLANS[code.plan].name : null, text: describeCode(code),
  });
}
