import type { MetadataRoute } from 'next';

const baseUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://inqsi.app';

const routes = [
  '',
  '/start-here',
  '/picks-audit',
  '/sports',
  '/sports/nfl',
  '/sports/cfb',
  '/sports/nba',
  '/sports/ncaam',
  '/sports/nhl',
  '/sports/mlb',
  '/sports/tennis',
  '/sports/soccer',
  '/predicted-winners',
  '/best-lines',
  '/parlay-scanner',
  '/live-market',
  '/performance',
  '/alerts',
  '/clv',
  '/watchlist',
  '/methodology',
  '/pricing',
  '/data-readiness',
  '/record-storage',
  '/account-readiness',
  '/operator',
  '/operator/creators',
  '/operator/members',
  '/operator/attribution',
  '/operator/data',
  '/operator/privacy',
  '/operator/support',
  '/release-checklist',
  '/legal/privacy',
  '/legal/cookies',
  '/privacy-choices',
  '/data-deletion',
  '/data-export',
  '/legal/disclaimer',
  '/legal/site-terms',
  '/legal/safe-use',
  '/legal/accessibility',
  '/contact'
];

export default function sitemap(): MetadataRoute.Sitemap {
  return routes.map((route) => ({
    url: `${baseUrl}${route}`,
    lastModified: new Date(),
    changeFrequency: route === '' || route === '/picks-audit' || route === '/predicted-winners' || route === '/live-market' ? 'daily' : 'weekly',
    priority: route === '' ? 1 : route === '/picks-audit' ? 0.95 : route === '/predicted-winners' ? 0.9 : 0.75
  }));
}
