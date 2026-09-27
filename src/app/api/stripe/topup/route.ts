import { NextResponse } from 'next/server';
import { z } from 'zod';
import { createAdmin, getUser } from '@/lib/supabase/server';
import { ensureCustomer, stripe } from '@/lib/stripe';
import { creditTopup } from '@/lib/billing';
import { PACKS } from '@/lib/pricing';

const Body = z.object({ pack: z.enum(['p100', 'p300', 'p600']), consent: z.literal(true) });

// Credits nachkaufen mit einem Klick (hinterlegte Karte). Button: „Jetzt zahlungspflichtig kaufen“.
export async function POST(request: Request) {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: 'Bitte bestätige den sofortigen Leistungsbeginn.' }, { status: 400 });
  const pack = PACKS[parsed.data.pack];
  const { data: p } = await createAdmin().from('profiles').select('name, default_payment_method, card_on_file').eq('id', user.id).single();
  if (!p?.card_on_file || !p.default_payment_method) return NextResponse.json({ error: 'Bitte zuerst eine Karte hinterlegen.' }, { status: 400 });

  const customer = await ensureCustomer(user.id, user.email!, p.name);
  const pi = await stripe.paymentIntents.create({
    amount: pack.price,
    currency: 'eur',
    customer,
    payment_method: p.default_payment_method,
    payment_method_types: ['card'],
    confirm: true,
    description: `Velox Cut – ${pack.credits} Credits`,
    metadata: { kind: 'topup', pack: parsed.data.pack, user_id: user.id, consent_sofortbeginn: new Date().toISOString() },
  }).catch((e) => e as { message?: string; raw?: { payment_intent?: { client_secret?: string } } });

  if (!('status' in pi)) return NextResponse.json({ error: pi.message || 'Die Zahlung wurde abgelehnt.' }, { status: 402 });
  if (pi.status === 'succeeded') {
    await creditTopup(pi);
    return NextResponse.json({ ok: true, credits: pack.credits });
  }
  if (pi.status === 'requires_action') return NextResponse.json({ clientSecret: pi.client_secret, id: pi.id });
  return NextResponse.json({ error: 'Die Zahlung wurde abgelehnt. Bitte prüfe deine Karte.' }, { status: 402 });
}
