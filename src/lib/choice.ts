// Paket-Auswahl von der Website (?paket=pro&intervall=yearly) durch Anmeldung und Karte mitnehmen.
import { PLANS, type Interval, type PlanId } from './pricing';

export type Choice = { paket: PlanId; intervall: Interval };

type Q = Record<string, string | string[] | undefined>;
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

export function parseChoice(q: Q): Choice {
  const p = one(q.paket);
  const paket = p && p in PLANS ? (p as PlanId) : 'free';
  const intervall: Interval = one(q.intervall) === 'yearly' ? 'yearly' : 'monthly';
  return { paket, intervall };
}

/** Query-String für die nächste Seite (leer bei Free) */
export const choiceQuery = (c: Choice) => (c.paket === 'free' ? '' : `?paket=${c.paket}&intervall=${c.intervall}`);

/** Wohin nach gespeicherter Karte: Free → Studio, bezahltes Paket → Abo bestätigen */
export const afterCard = (c: Choice) => (c.paket === 'free' ? '/studio?willkommen=1' : `/konto${choiceQuery(c)}#abo`);

/** Nur interne Pfade als Weiterleitungsziel zulassen (kein //andere-seite.de) */
export const safePath = (v: unknown, fallback = '/studio') =>
  typeof v === 'string' && v.startsWith('/') && !v.startsWith('//') && !v.startsWith('/\\') ? v : fallback;
