import { NextResponse } from 'next/server';
import { createAdmin, getUser } from '@/lib/supabase/server';
import { stripe } from '@/lib/stripe';

// Stripe-Kundenportal: Rechnungen, Karte ändern, Paket wechseln
export async function POST(request: Request) {
  const user = await getUser();
  if (!user) return NextResponse.redirect(new URL('/login', request.url), { status: 303 });
  const { data: p } = await createAdmin().from('profiles').select('stripe_customer_id').eq('id', user.id).single();
  if (!p?.stripe_customer_id) return NextResponse.redirect(new URL('/konto', request.url), { status: 303 });
  const s = await stripe.billingPortal.sessions.create({ customer: p.stripe_customer_id, return_url: new URL('/konto', request.url).toString(), locale: 'de' });
  return NextResponse.redirect(s.url, { status: 303 });
}
