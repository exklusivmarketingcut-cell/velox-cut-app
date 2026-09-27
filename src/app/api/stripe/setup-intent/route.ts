import { NextResponse } from 'next/server';
import { getUser, createAdmin } from '@/lib/supabase/server';
import { ensureCustomer, stripe } from '@/lib/stripe';

// Karte für spätere Käufe speichern – es wird nichts abgebucht
export async function POST() {
  const user = await getUser();
  if (!user) return NextResponse.json({ error: 'login' }, { status: 401 });
  const { data: p } = await createAdmin().from('profiles').select('name').eq('id', user.id).single();
  const customer = await ensureCustomer(user.id, user.email!, p?.name);
  const si = await stripe.setupIntents.create({
    customer,
    usage: 'off_session',
    payment_method_types: ['card'], // Kreditkarte, Apple Pay, Google Pay
    metadata: { user_id: user.id },
  });
  return NextResponse.json({ clientSecret: si.client_secret });
}
