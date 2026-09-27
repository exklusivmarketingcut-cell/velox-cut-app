import 'server-only';
import { redirect } from 'next/navigation';
import { createAdmin, createClient, getUser } from './supabase/server';
import { PLANS } from './pricing';

export type Profile = {
  id: string; email: string; name: string | null; stripe_customer_id: string | null; default_payment_method: string | null;
  card_on_file: boolean; plan: 'free' | 'starter' | 'pro' | 'business'; plan_interval: 'monthly' | 'yearly' | null;
  plan_status: string | null; subscription_id: string | null; plan_credits: number; topup_credits: number; free_refill_at: string;
};

/** Eingeloggtes Profil laden; ohne Login → /login, ohne Karte (optional) → /karte. */
export async function requireProfile(opts: { needCard?: boolean } = {}): Promise<Profile> {
  const user = await getUser();
  if (!user) redirect('/login');
  const admin = createAdmin();
  const { data: cur } = await admin.from('profiles').select('plan').eq('id', user.id).single();
  if (cur) await admin.rpc('refresh_credits', { p_user: user.id, p_plan_amount: PLANS[cur.plan as keyof typeof PLANS].credits });
  const supabase = await createClient();
  const { data } = await supabase.from('profiles').select('*').eq('id', user.id).single();
  if (!data) redirect('/login');
  if (opts.needCard && !data.card_on_file) redirect('/karte');
  return data as Profile;
}
