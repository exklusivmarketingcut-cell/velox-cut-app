import 'server-only';

const FROM = process.env.EMAIL_FROM || 'Velox Cut <noreply@velox-cut.de>';
export const OWNER = process.env.OWNER_EMAIL || 'infoveloxcut@gmail.com'; // Empfänger der Betreiber-Mails
/** Öffentliche Adresse für Kunden (Antworten, Texte) – leitet ins Postfach des Betreibers weiter */
export const SUPPORT = 'info@velox-cut.de';

/** E-Mail über Resend senden. Ohne API-Key: nur ins Log schreiben (lokales Testen). */
export async function sendMail(to: string, subject: string, text: string, replyTo?: string) {
  if (!process.env.RESEND_API_KEY) {
    console.log(`[E-Mail nicht gesendet – RESEND_API_KEY fehlt] an ${to}: ${subject}\n${text}`);
    return false;
  }
  const r = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: { Authorization: `Bearer ${process.env.RESEND_API_KEY}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ from: FROM, to: [to], subject, text, ...(replyTo ? { reply_to: replyTo } : {}) }),
  });
  if (!r.ok) console.error('E-Mail fehlgeschlagen', r.status, await r.text());
  return r.ok;
}
