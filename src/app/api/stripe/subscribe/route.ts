import { NextResponse } from 'next/server';
import { z } from 'zod';
import { createAdmin, getUser } from '@/lib/supabase/server';
import { ensureCustomer, priceByLookup, stripe } from '@/lib/stripe';
import { lookupKey } from '@/lib/pricing';
import { CODE_ERR, cardFingerprint, findCode } from '@/lib/codes';

const Body = z.object({ plan: z.enum(['starter', 'pro', 'business']), interval: z.enum(['monthly', 'yearly']), consent: z.literal(true), code: z.string().max(40).optional() });

// Abo abschließen mit der hinterlegten Karte. Button im Client: „Jetzt zahlungspflichtig abonnieren“.
export async function POST(request: Request) {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: 'Bitte bestätige den sofortigen Leistungsbeginn.' }, { status: 400 });
  let { plan, interval } = parsed.data;
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

  // Gratis-Monat per Code: Paket aus dem Code, monatlich, 30 Tage Testzeitraum bei Stripe (danach regulär, kündbar).
  // Erst einlösen (atomar, je Konto und Karte einmal), bei einem Stripe-Fehler wieder zurücknehmen.
  let code: string | null = null;
  if (parsed.data.code) {
    const f = await findCode(parsed.data.code);
    if (!f.code) return NextResponse.json({ error: f.error }, { status: 400 });
    if (f.code.art !== 'gratis_monat') return NextResponse.json({ error: 'Dieser Code schenkt Credits – lös ihn oben unter „Gutschein-Code“ ein.' }, { status: 400 });
    plan = f.code.plan!;
    interval = 'monthly';
    const { data: res } = await admin.rpc('redeem_code', { p_code: f.code.code, p_user: user.id, p_card: await cardFingerprint(p.default_payment_method), p_art: 'gratis_monat' });
    if (res !== 'ok') return NextResponse.json({ error: CODE_ERR[res as string] ?? 'Code nicht einlösbar.' }, { status: 400 });
    code = f.code.code;
  }

  const customer = await ensureCustomer(user.id, user.email!, p.name);
  const price = await priceByLookup(lookupKey.plan(plan, interval));
  let sub;
  try {
    sub = await stripe.subscriptions.create({
      customer,
      items: [{ price: price.id }],
      default_payment_method: p.default_payment_method,
      payment_behavior: 'default_incomplete',
      payment_settings: { payment_method_types: ['card'], save_default_payment_method: 'on_subscription' },
      ...(code ? { trial_period_days: 30, trial_settings: { end_behavior: { missing_payment_method: 'cancel' as const } } } : {}),
      metadata: { user_id: user.id, consent_sofortbeginn: new Date().toISOString(), ...(code ? { code } : {}) },
      expand: ['latest_invoice.confirmation_secret'],
    });
  } catch (e) {
    if (code) await admin.rpc('unredeem_code', { p_code: code, p_user: user.id });
    throw e;
  }
  await admin.from('profiles').update({ subscription_id: sub.id, plan_status: sub.status }).eq('id', user.id);

  const inv = sub.latest_invoice;
  const secret = inv && typeof inv !== 'string' ? inv.confirmation_secret?.client_secret : null;
  if (sub.status === 'trialing' || (inv && typeof inv !== 'string' && inv.status === 'paid')) return NextResponse.json({ ok: true, trial: sub.status === 'trialing' });
  // Karte muss noch bestätigt werden (z. B. 3-D Secure) → im Browser abschließen
  return NextResponse.json({ clientSecret: secret, paymentMethod: p.default_payment_method });
}
