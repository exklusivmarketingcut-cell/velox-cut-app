import 'server-only';
import Stripe from 'stripe';
import { createAdmin } from './supabase/server';

// Erst beim ersten Aufruf erzeugen (der Build läuft so auch ohne Schlüssel)
let client: Stripe | undefined;
export const stripe = new Proxy({} as Stripe, {
  get(_t, prop) {
    client ??= new Stripe(process.env.STRIPE_SECRET_KEY!, demoHost());
    return Reflect.get(client, prop, client);
  },
});

/** Nur für die lokale Vorführung (demo/backend.mjs): Stripe-Anfragen an die Nachbildung statt an api.stripe.com */
function demoHost(): Stripe.StripeConfig {
  const host = process.env.STRIPE_API_HOST;
  return host ? { host, port: Number(process.env.STRIPE_API_PORT), protocol: 'http' } : {};
}

const priceCache = new Map<string, Stripe.Price>();
/** Preis über seinen Lookup-Key holen (siehe scripts/stripe-setup.mjs). */
export async function priceByLookup(key: string): Promise<Stripe.Price> {
  const hit = priceCache.get(key);
  if (hit) return hit;
  const { data } = await stripe.prices.list({ lookup_keys: [key], active: true, limit: 1 });
  if (!data[0]) throw new Error(`Stripe-Preis ${key} fehlt – scripts/stripe-setup.mjs ausführen`);
  priceCache.set(key, data[0]);
  return data[0];
}

/** Stripe-Kunden zum Profil holen oder anlegen. */
export async function ensureCustomer(userId: string, email: string, name?: string | null): Promise<string> {
  const admin = createAdmin();
  const { data: p } = await admin.from('profiles').select('stripe_customer_id').eq('id', userId).single();
  if (p?.stripe_customer_id) return p.stripe_customer_id;
  const c = await stripe.customers.create({ email, name: name ?? undefined, metadata: { user_id: userId } });
  await admin.from('profiles').update({ stripe_customer_id: c.id }).eq('id', userId);
  return c.id;
}
