// Legt Produkte und Preise in Stripe an (einmalig, darf mehrfach laufen – vorhandene Lookup-Keys werden übersprungen).
// Aufruf:  STRIPE_SECRET_KEY=sk_test_... node scripts/stripe-setup.mjs
import Stripe from 'stripe';

const key = process.env.STRIPE_SECRET_KEY;
if (!key) { console.error('STRIPE_SECRET_KEY fehlt'); process.exit(1); }
const stripe = new Stripe(key);

// Muss zu src/lib/pricing.ts passen
const PLANS = { starter: ['Starter', 200, 1490, 14900], pro: ['Pro', 500, 2990, 29900], business: ['Business', 900, 5990, 59900] };
const PACKS = { p100: [100, 990], p300: [300, 2790], p600: [600, 4990] };

async function exists(lookup) {
  const { data } = await stripe.prices.list({ lookup_keys: [lookup], limit: 1 });
  return data[0];
}

for (const [id, [name, credits, monthly, yearly]] of Object.entries(PLANS)) {
  let product;
  for (const [interval, amount] of [['monthly', monthly], ['yearly', yearly]]) {
    const lookup = `velox_${id}_${interval}`;
    if (await exists(lookup)) { console.log('✓ vorhanden', lookup); continue; }
    product ??= await stripe.products.create({ name: `Velox Cut ${name}`, description: `${credits} Credits pro Monat`, metadata: { plan: id } });
    await stripe.prices.create({ product: product.id, currency: 'eur', unit_amount: amount, lookup_key: lookup, recurring: { interval: interval === 'monthly' ? 'month' : 'year' }, tax_behavior: 'inclusive' });
    console.log('＋ angelegt', lookup, amount / 100, '€');
  }
}
for (const [id, [credits, amount]] of Object.entries(PACKS)) {
  const lookup = `velox_${id}`;
  if (await exists(lookup)) { console.log('✓ vorhanden', lookup); continue; }
  const product = await stripe.products.create({ name: `Velox Cut ${credits} Credits`, metadata: { pack: id } });
  await stripe.prices.create({ product: product.id, currency: 'eur', unit_amount: amount, lookup_key: lookup, tax_behavior: 'inclusive' });
  console.log('＋ angelegt', lookup, amount / 100, '€');
}
console.log('\nFertig. Jetzt im Stripe-Dashboard den Webhook anlegen (siehe SETUP.md).');
