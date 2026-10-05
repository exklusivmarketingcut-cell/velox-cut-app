// Betreiber-Zugang für /admin. Weitere Adressen über ADMIN_EMAILS (kommagetrennt) freischalten.
const ADMINS = (process.env.ADMIN_EMAILS || 'infoveloxcut@gmail.com,exklusivmarketing.cut@gmail.com')
  .split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);

export const isAdmin = (email: string | null | undefined) => !!email && ADMINS.includes(email.toLowerCase());
