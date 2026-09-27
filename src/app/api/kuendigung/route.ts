import { NextResponse } from 'next/server';
import { z } from 'zod';
import { createAdmin } from '@/lib/supabase/server';
import { stripe } from '@/lib/stripe';
import { OWNER, sendMail } from '@/lib/email';

const Body = z.object({
  art: z.string().min(1).max(80),
  grund: z.string().max(2000).optional(),
  name: z.string().trim().min(1).max(120),
  email: z.email().max(200),
  paket: z.string().min(1).max(60),
  vertragsnummer: z.string().max(80).optional(),
  zeitpunkt: z.string().min(1).max(60),
  datum: z.string().max(20).optional(),
  abgegeben: z.string().max(80).optional(),
  'bot-field': z.string().max(0).optional(),
});

const fmt = (d: Date) => d.toLocaleString('de-DE', { dateStyle: 'long', timeStyle: 'short', timeZone: 'Europe/Berlin' }) + ' Uhr';
const day = (d: Date) => d.toLocaleDateString('de-DE', { dateStyle: 'long', timeZone: 'Europe/Berlin' });

// Kündigungsbutton (§ 312k BGB): speichern, Abo beenden, Eingang sofort per E-Mail bestätigen
export async function POST(request: Request) {
  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: 'Bitte alle Pflichtfelder ausfüllen.' }, { status: 400 });
  const b = parsed.data;
  const eingang = new Date();
  const admin = createAdmin();
  await admin.from('cancellations').insert({ art: b.art, grund: b.grund, name: b.name, email: b.email, paket: b.paket, vertragsnummer: b.vertragsnummer, zeitpunkt: b.zeitpunkt, datum: b.datum, abgegeben: b.abgegeben });

  const ausserordentlich = b.art.startsWith('Außer');
  let ende = 'Wir prüfen deine Kündigung und bestätigen dir das genaue Vertragsende innerhalb von 24 Stunden.';
  const { data: p } = await admin.from('profiles').select('id, subscription_id, plan, plan_status').ilike('email', b.email).maybeSingle();

  if (p?.subscription_id && p.plan !== 'free' && p.plan_status !== 'canceled') {
    try {
      const sub = await stripe.subscriptions.retrieve(p.subscription_id);
      const periodEnd = new Date((sub.items.data[0]?.current_period_end ?? 0) * 1000);
      if (ausserordentlich) {
        ende = 'Außerordentliche Kündigung: Wir prüfen den angegebenen Grund und bestätigen dir das Vertragsende innerhalb von 24 Stunden.';
      } else {
        const wunsch = b.zeitpunkt.startsWith('Zu einem') && b.datum ? new Date(`${b.datum}T23:59:00+02:00`) : null;
        if (wunsch && wunsch > periodEnd) {
          await stripe.subscriptions.update(sub.id, { cancel_at: Math.floor(wunsch.getTime() / 1000) });
          ende = `Dein Vertrag endet zu deinem Wunschtermin am ${day(wunsch)}.`;
        } else {
          await stripe.subscriptions.update(sub.id, { cancel_at_period_end: true });
          ende = `Dein Vertrag endet zum nächstmöglichen Zeitpunkt am ${day(periodEnd)}. Bis dahin kannst du Velox Cut ganz normal nutzen.`;
        }
      }
    } catch (e) {
      console.error('Kündigung: Abo nicht angepasst', e);
    }
  } else if (p) {
    ende = 'Unter dieser E-Mail-Adresse besteht kein bezahltes Abo. Dein Gratis-Zugang endet sofort; auf Wunsch löschen wir dein Konto.';
  }

  const inhalt = [
    `Art der Kündigung: ${b.art}${b.grund ? `\nKündigungsgrund: ${b.grund}` : ''}`,
    `Name: ${b.name}`, `E-Mail: ${b.email}`, `Paket: ${b.paket}${b.vertragsnummer ? `\nKunden-/Rechnungsnummer: ${b.vertragsnummer}` : ''}`,
    `Gewünschtes Ende: ${b.zeitpunkt}${b.datum ? ` (${b.datum})` : ''}`,
  ].join('\n');
  const text = `Hallo ${b.name},\n\nwir bestätigen den Eingang deiner Kündigung am ${fmt(eingang)}.\n\n${ende}\n\nDeine Angaben:\n${inhalt}\n\nNachgekaufte Credits behältst du, solange dein Konto besteht. Deine Beta-Preisgarantie endet mit dem Vertrag.\n\nViele Grüße\nNunu Alexandru Petru Pasca · Velox Cut\nWeiherstraße 23 · 73730 Esslingen am Neckar · infoveloxcut@gmail.com`;
  const sent = await sendMail(b.email, 'Bestätigung deiner Kündigung – Velox Cut Studio', text, OWNER);
  await sendMail(OWNER, `Kündigung: ${b.name} (${b.paket})`, `${inhalt}\n\nEingang: ${fmt(eingang)}\nAutomatisch: ${ende}\nBestätigung an Kunden gesendet: ${sent ? 'ja' : 'NEIN – bitte manuell bestätigen!'}`);
  return NextResponse.json({ ok: true, ende });
}
