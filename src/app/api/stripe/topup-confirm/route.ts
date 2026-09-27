import { NextResponse } from 'next/server';
import { getUser } from '@/lib/supabase/server';
import { stripe } from '@/lib/stripe';
import { creditTopup } from '@/lib/billing';

// Nach 3-D Secure im Browser: Zahlung prüfen und Credits sofort gutschreiben (der Webhook macht es sonst auch)
export async function POST(request: Request) {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const { id } = await request.json();
  const pi = await stripe.paymentIntents.retrieve(String(id));
  if (pi.metadata?.user_id !== user.id) return NextResponse.json({ error: 'nicht gefunden' }, { status: 404 });
  if (pi.status !== 'succeeded') return NextResponse.json({ error: 'Zahlung nicht abgeschlossen.' }, { status: 402 });
  await creditTopup(pi);
  return NextResponse.json({ ok: true });
}
