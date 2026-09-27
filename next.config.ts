import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  // Die Website liegt als statisches HTML in public/ (Quelle: ~/veloxcut-studio, scripts/sync-site.sh)
  async rewrites() {
    return {
      beforeFiles: [{ source: '/', destination: '/index.html' }],
      afterFiles: [],
      fallback: [],
    };
  },
  async redirects() {
    return [{ source: '/index.html', destination: '/', permanent: true }];
  },
};

export default nextConfig;
