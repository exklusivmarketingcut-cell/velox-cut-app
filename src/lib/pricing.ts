// Einzige Quelle für Preise, Credits und Stil-Kosten.
// Website (public/index.html), App und Stripe-Setup müssen zu diesen Werten passen.

export type PlanId = 'free' | 'starter' | 'pro' | 'business';
export type Interval = 'monthly' | 'yearly';
export type StyleId = 'clean' | 'viral';
export type PackId = 'p100' | 'p300' | 'p600';

export const PLANS: Record<PlanId, { name: string; credits: number; monthly: number; yearly: number }> = {
  free: { name: 'Free', credits: 10, monthly: 0, yearly: 0 },
  starter: { name: 'Starter', credits: 200, monthly: 1490, yearly: 14900 },
  pro: { name: 'Pro', credits: 500, monthly: 2990, yearly: 29900 },
  business: { name: 'Business', credits: 900, monthly: 5990, yearly: 59900 },
};

export const PACKS: Record<PackId, { credits: number; price: number }> = {
  p100: { credits: 100, price: 990 },
  p300: { credits: 300, price: 2790 },
  p600: { credits: 600, price: 4990 },
};

// Credits pro fertigem Kurzvideo (bis 60 Sek.) je Stil. Premium kommt später.
export const STYLES: Record<StyleId, { name: string; credits: number; desc: string }> = {
  clean: { name: 'Clean', credits: 10, desc: 'Sauberer Schnitt, Untertitel, leichte Zooms' },
  viral: { name: 'Viral', credits: 20, desc: 'Zoom-Punches, Sounds, Emojis, Hook-Titel' },
};

export const EXTRA_MINUTE_CREDITS = 5;
export const MAX_CLIPS_PER_JOB = 5;
export const MAX_UPLOAD_BYTES = 2 * 1024 ** 3; // 2 GB

/** Credits für einen Auftrag: pro Clip der Stil-Preis, +5 je angefangener Minute über 60 Sek. */
export function jobCost(style: StyleId, clips: number, clipSeconds = 60): number {
  const extraMin = Math.max(0, Math.ceil((clipSeconds - 60) / 60));
  return clips * (STYLES[style].credits + extraMin * EXTRA_MINUTE_CREDITS);
}

/** Stripe-Lookup-Keys: so findet der Server die Preise, ohne IDs in Umgebungsvariablen zu pflegen. */
export const lookupKey = {
  plan: (plan: Exclude<PlanId, 'free'>, interval: Interval) => `velox_${plan}_${interval}`,
  pack: (pack: PackId) => `velox_${pack}`,
};

export const euro = (cents: number) => (cents / 100).toLocaleString('de-DE', { style: 'currency', currency: 'EUR' });
