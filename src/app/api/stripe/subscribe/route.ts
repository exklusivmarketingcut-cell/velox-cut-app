import { NextResponse } from 'next/server';
import { z } from 'zod';
import { createAdmin, getUser } from '@/lib/supabase/server';
import { ensureCustomer, priceByLookup, stripe } from '@/lib/stripe';
import { lookupKey } from '@/lib/pricing';

const Body = z.object({ plan: z.enum(['starter', 'pro', 'business']), interval: z.enum(['monthly', 'yearly']), consent: z.literal(true) });

// Abo abschließen mit der hinterlegten Karte. Button im Client: „Jetzt zahlungspflichtig abonnieren“.
export async function POST(request: Request) {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: 'Bitte bestätige den sofortigen Leistungsbeginn.' }, { status: 400 });
  const { plan, interval } = parsed.data;
  const admin = createAdmin();
  const { data: p } = await admin.from('profiles').select('*').eq('id', user.id).single();
  if (!p?.card_on_file || !p.default_payment_method) return NextResponse.json({ error: 'Bitte zuerst eine Karte hinterlegen.' }, { status: 400 });
  if (p.subscription_id && p.plan_status === 'incomplete') {
    // abgebrochener Versuch (z. B. 3-D Secure nicht bestätigt) → aufräumen und neu starten
    await stripe.subscriptions.cancel(p.subscription_id).catch(() => null);
    await admin.from('profiles').update({ subscription_id: null, plan_status: null }).eq('id', user.id);
  } else if (p.subscription_id && p.plan_status !== 'canceled') {
    return NextResponse.json({ error: 'Du hast schon ein Abo. Paket wechseln kannst du unter „Rechnungen & Abo verwalten“.' }, { status: 409 });
  }

  const customer = await ensureCustomer(user.id, user.email!, p.name);
  const price = await priceByLookup(lookupKey.plan(plan, interval));
  const sub = await stripe.subscriptions.create({
    customer,
    items: [{ price: price.id }],
    default_payment_method: p.default_payment_method,
    payment_behavior: 'default_incomplete',
    payment_settings: { payment_method_types: ['card'], save_default_payment_method: 'on_subscription' },
    metadata: { user_id: user.id, consent_sofortbeginn: new Date().toISOString() },
    expand: ['latest_invoice.confirmation_secret'],
  });
  await admin.from('profiles').update({ subscription_id: sub.id, plan_status: sub.status }).eq('id', user.id);

  const inv = sub.latest_invoice;
  const secret = inv && typeof inv !== 'string' ? inv.confirmation_secret?.client_secret : null;
  if (inv && typeof inv !== 'string' && inv.status === 'paid') return NextResponse.json({ ok: true });
  // Karte muss noch bestätigt werden (z. B. 3-D Secure) → im Browser abschließen
  return NextResponse.json({ clientSecret: secret, paymentMethod: p.default_payment_method });
}
