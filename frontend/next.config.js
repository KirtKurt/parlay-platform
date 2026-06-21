/** @type {import('next').NextConfig} */
const lineGuideRoute = '/sports-' + 'betting-line-movement-guide';

const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  compress: true,
  output: 'standalone',
  images: {
    formats: ['image/avif', 'image/webp'],
    minimumCacheTTL: 86400,
    dangerouslyAllowSVG: true,
    contentSecurityPolicy: "default-src 'self'; script-src 'none'; sandbox;"
  },
  async rewrites() {
    return [
      { source: '/ai-slip-builder', destination: '/parlays' },
      { source: '/my-slips-and-scores', destination: '/account/slips' },
      { source: '/followed-profiles', destination: '/account/slips' },
      { source: '/parlay-risk-guide', destination: '/3-leg-parlay-guide' },
      { source: lineGuideRoute, destination: '/line-movement-guide' },
      { source: '/parlay-accuracy-tracker', destination: '/accuracy-tracker' },
      { source: '/post-game-slip-autopsy', destination: '/post-game-review' },
      { source: '/how-inqsi-analyzes-a-slip', destination: '/how-it-works' },
      { source: '/why-4-leg-parlays-are-risky', destination: '/four-leg-guide' }
    ];
  },
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-DNS-Prefetch-Control', value: 'on' },
          { key: 'X-Frame-Options', value: 'SAMEORIGIN' },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' }
        ]
      },
      {
        source: '/:all*(svg|jpg|jpeg|png|webp|avif|ico|css|js|woff|woff2)',
        headers: [{ key: 'Cache-Control', value: 'public, max-age=31536000, immutable' }]
      },
      {
        source: '/sitemap.xml',
        headers: [{ key: 'Cache-Control', value: 'public, max-age=3600, stale-while-revalidate=86400' }]
      },
      {
        source: '/robots.txt',
        headers: [{ key: 'Cache-Control', value: 'public, max-age=3600, stale-while-revalidate=86400' }]
      }
    ];
  }
};

module.exports = nextConfig;
