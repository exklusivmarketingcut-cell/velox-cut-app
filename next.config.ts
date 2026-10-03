import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  // Die Website liegt als statisches HTML in public/ (Quelle: Ordner „webseite“ neben „app“, scripts/sync-site.sh)
  async rewrites() {
    return {
      beforeFiles: [{ source: '/', destination: '/index.html' }],
      afterFiles: [],
      fallback: [],
    };
  },
  async redirects() {
    return [
      { source: '/index.html', destination: '/', permanent: true },
      // www.velox-cut.de → velox-cut.de (eine Adresse für alles)
      { source: '/:path*', has: [{ type: 'host', value: 'www.velox-cut.de' }], destination: 'https://velox-cut.de/:path*', permanent: true },
      // alte workers.dev-Adresse → velox-cut.de (nur der Stripe-Webhook bleibt dort erreichbar)
      { source: '/:path((?!api/stripe/webhook).*)', has: [{ type: 'host', value: 'veloxcut.veloxcut-app.workers.dev' }], destination: 'https://velox-cut.de/:path', permanent: false },
    ];
  },
};

export default nextConfig;
