import { NextResponse } from 'next/server';
import { getUser, createAdmin } from '@/lib/supabase/server';
import { stripe } from '@/lib/stripe';

// Nach erfolgreicher Karteneingabe: Karte als Standard setzen und im Profil vermerken
export async function POST(request: Request) {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const { setupIntentId } = await request.json();
  const si = await stripe.setupIntents.retrieve(String(setupIntentId));
  const admin = createAdmin();
  const { data: p } = await admin.from('profiles').select('stripe_customer_id').eq('id', user.id).single();
  if (si.status !== 'succeeded' || !si.payment_method || si.customer !== p?.stripe_customer_id) {
    return NextResponse.json({ error: 'Karte konnte nicht bestätigt werden.' }, { status: 400 });
  }
  const pm = typeof si.payment_method === 'string' ? si.payment_method : si.payment_method.id;
  await stripe.customers.update(p!.stripe_customer_id!, { invoice_settings: { default_payment_method: pm } });
  await admin.from('profiles').update({ card_on_file: true, default_payment_method: pm }).eq('id', user.id);
  return NextResponse.json({ ok: true });
}
