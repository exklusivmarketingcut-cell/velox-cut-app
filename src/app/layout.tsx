import type { Metadata } from 'next';
import './app.css';

export const metadata: Metadata = {
  title: { default: 'Velox Cut Studio', template: '%s – Velox Cut Studio' },
  description: 'Fertig geschnittene Clips und Reels in Minuten.',
  icons: { icon: '/assets/img/favicon.png' },
};

export default function RootLayout({ children }: LayoutProps<'/'>) {
  return (
    <html lang="de">
      <head>
        {/* Schriften liegen in public/ (von der Website mitgenutzt) */}
        {/* eslint-disable-next-line @next/next/no-css-tags */}
        <link rel="stylesheet" href="/assets/fonts.css" />
      </head>
      <body>
        {process.env.NEXT_PUBLIC_DEMO === '1' ? (
          <div style={{ background: '#fde68a', color: '#1a1300', textAlign: 'center', font: '600 13px/1.4 system-ui', padding: '6px 12px' }}>
            DEMO-Modus · lokale Nachbildung von Supabase &amp; Stripe · keine echten Zahlungen
          </div>
        ) : null}
        {children}
      </body>
    </html>
  );
}
