import 'server-only';
import type Stripe from 'stripe';
import { createAdmin } from './supabase/server';
import { PACKS, PLANS, type Interval, type PackId, type PlanId } from './pricing';

/** Paket + Intervall aus dem Lookup-Key eines Preises lesen (velox_pro_monthly). */
export function planFromPrice(price?: Stripe.Price | null): { plan: Exclude<PlanId, 'free'>; interval: Interval } | null {
  const m = /^velox_(starter|pro|business)_(monthly|yearly)$/.exec(price?.lookup_key ?? '');
  return m ? { plan: m[1] as Exclude<PlanId, 'free'>, interval: m[2] as Interval } : null;
}

/** Nachkauf gutschreiben – mehrfacher Aufruf mit derselben Zahlung schreibt nur einmal gut. */
export async function creditTopup(pi: Stripe.PaymentIntent): Promise<boolean> {
  if (pi.status !== 'succeeded' || pi.metadata?.kind !== 'topup') return false;
  const pack = PACKS[pi.metadata.pack as PackId];
  if (!pack || pi.amount !== pack.price) return false;
  const { data } = await createAdmin().rpc('grant_topup', { p_user: pi.metadata.user_id, p_amount: pack.credits, p_ref: pi.id });
  return !!data;
}

/** Abo-Stand ins Profil übernehmen. Paket gilt erst, wenn das Abo aktiv (bezahlt) ist. */
export async function syncSubscription(sub: Stripe.Subscription) {
  const userId = sub.metadata?.user_id;
  if (!userId) return;
  const admin = createAdmin();
  const { data: p } = await admin.from('profiles').select('plan, subscription_id').eq('id', userId).single();
  if (!p) return;
  const isCurrent = !p.subscription_id || p.subscription_id === sub.id;
  if (!isCurrent) return; // alte/fremde Abos ignorieren

  if (sub.status === 'canceled' || sub.status === 'incomplete_expired') {
    if (p.plan !== 'free') await admin.rpc('downgrade_to_free', { p_user: userId });
    else await admin.from('profiles').update({ plan_status: sub.status, subscription_id: null }).eq('id', userId);
    return;
  }
  const info = planFromPrice(sub.items.data[0]?.price);
  if (['active', 'trialing', 'past_due'].includes(sub.status) && info) {
    await admin.from('profiles').update({ plan: info.plan, plan_interval: info.interval, plan_status: sub.status, subscription_id: sub.id }).eq('id', userId);
  } else {
    // z. B. incomplete: Zahlung läuft noch – Paket noch nicht freischalten
    await admin.from('profiles').update({ plan_status: sub.status, subscription_id: sub.id }).eq('id', userId);
  }
}

export const planCredits = (plan: PlanId) => PLANS[plan].credits;
