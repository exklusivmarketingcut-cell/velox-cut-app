import { NextResponse } from 'next/server';
import Stripe from 'stripe';

const cryptoProvider = Stripe.createSubtleCryptoProvider();
import { stripe } from '@/lib/stripe';
import { createAdmin } from '@/lib/supabase/server';
import { creditTopup, planFromPrice, syncSubscription } from '@/lib/billing';
import { PLANS } from '@/lib/pricing';

// Stripe meldet hier Zahlungen und Abo-Änderungen. In Stripe als Endpoint eintragen: https://velox-cut.de/api/stripe/webhook
export async function POST(request: Request) {
  const sig = request.headers.get('stripe-signature');
  const body = await request.text();
  let event: Stripe.Event;
  try {
    // Web-Crypto-Variante (läuft auf Cloudflare Workers)
    event = await stripe.webhooks.constructEventAsync(body, sig!, process.env.STRIPE_WEBHOOK_SECRET!, undefined, cryptoProvider);
  } catch {
    return NextResponse.json({ error: 'Signatur ungültig' }, { status: 400 });
  }
  const admin = createAdmin();
  // jede Meldung nur einmal verarbeiten
  const { error: dup } = await admin.from('stripe_events').insert({ id: event.id, type: event.type });
  if (dup) return NextResponse.json({ ok: true, duplicate: true });

  try {
    switch (event.type) {
      case 'invoice.paid': {
        const inv = event.data.object;
        const subId = inv.parent?.subscription_details?.subscription;
        if (!subId) break;
        const sub = await stripe.subscriptions.retrieve(typeof subId === 'string' ? subId : subId.id);
        const info = planFromPrice(sub.items.data[0]?.price);
        const userId = sub.metadata?.user_id;
        if (!info || !userId) break;
        await syncSubscription(sub);
        // neuer Abrechnungszeitraum → Paket-Credits auf die Paketmenge (Jahres-Abo: danach monatlich über refresh_credits)
        await admin.rpc('reset_plan_credits', { p_user: userId, p_amount: PLANS[info.plan].credits, p_ref: inv.id });
        break;
      }
      case 'customer.subscription.created':
      case 'customer.subscription.updated':
      case 'customer.subscription.deleted':
        await syncSubscription(event.data.object);
        break;
      case 'payment_intent.succeeded':
        await creditTopup(event.data.object);
        break;
      case 'setup_intent.succeeded': {
        const si = event.data.object;
        const userId = si.metadata?.user_id;
        const pm = typeof si.payment_method === 'string' ? si.payment_method : si.payment_method?.id;
        if (userId && pm) await admin.from('profiles').update({ card_on_file: true, default_payment_method: pm }).eq('id', userId);
        break;
      }
    }
  } catch (e) {
    // Fehler → Meldung wieder freigeben, damit Stripe sie erneut schickt
    await admin.from('stripe_events').delete().eq('id', event.id);
    console.error('Webhook-Fehler', event.type, e);
    return NextResponse.json({ error: 'Verarbeitung fehlgeschlagen' }, { status: 500 });
  }
  return NextResponse.json({ ok: true });
}
