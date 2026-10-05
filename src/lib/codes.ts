import 'server-only';
import { createAdmin } from './supabase/server';
import { stripe } from './stripe';
import { PLANS, euro } from './pricing';

export type Code = {
  code: string; art: 'credits' | 'gratis_monat'; credits: number; plan: 'starter' | 'pro' | 'business' | null;
  max_uses: number | null; uses: number; valid_until: string | null; active: boolean; note: string | null;
};

export const normCode = (c: unknown) => String(c ?? '').trim().toUpperCase().replace(/\s+/g, '');

/** Kundenfreundliche Meldungen zu den Ergebnissen von redeem_code() */
export const CODE_ERR: Record<string, string> = {
  unbekannt: 'Diesen Code gibt es nicht. Bitte prüf die Schreibweise.',
  inaktiv: 'Dieser Code ist nicht mehr aktiv.',
  abgelaufen: 'Dieser Code ist abgelaufen.',
  aufgebraucht: 'Dieser Code wurde schon zu oft eingelöst.',
  schon_eingeloest: 'Du hast diesen Code schon eingelöst.',
  karte_schon_genutzt: 'Mit dieser Karte wurde der Code schon eingelöst.',
  falsche_art: 'Dieser Code passt hier nicht.',
};

/** Code laden und prüfen, ob er gerade einlösbar ist (ohne einzulösen). */
export async function findCode(raw: unknown): Promise<{ code?: Code; error?: string }> {
  const c = normCode(raw);
  if (!/^[A-Z0-9_-]{3,30}$/.test(c)) return { error: CODE_ERR.unbekannt };
  const { data, error } = await createAdmin().from('codes').select('*').eq('code', c).maybeSingle();
  if (error) return { error: 'Codes sind gerade nicht verfügbar.' };
  if (!data) return { error: CODE_ERR.unbekannt };
  const code = data as Code;
  if (!code.active) return { error: CODE_ERR.inaktiv };
  if (code.valid_until && new Date(code.valid_until) < new Date()) return { error: CODE_ERR.abgelaufen };
  if (code.max_uses !== null && code.uses >= code.max_uses) return { error: CODE_ERR.aufgebraucht };
  return { code };
}

/** Was der Code bringt – für Studio, Konto und Admin */
export function describeCode(c: Code) {
  if (c.art === 'credits') return `${c.credits} Credits geschenkt (bis zu ${Math.floor(c.credits / 10)} Videos) – ohne Abo`;
  const p = PLANS[c.plan!];
  return `${p.name} 1 Monat gratis (${p.credits} Credits) – danach ${euro(p.monthly)} / Monat, monatlich kündbar`;
}

/** Fingerabdruck der hinterlegten Karte: derselbe Code lässt sich mit derselben Karte nur einmal einlösen */
export async function cardFingerprint(pm: string | null | undefined) {
  if (!pm) return null;
  const m = await stripe.paymentMethods.retrieve(pm).catch(() => null);
  return m?.card?.fingerprint ?? null;
}
