import type { MetadataRoute } from 'next';

const baseUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://inqsi.app';
const movementRoute = '/line-' + 'movement-review';
const lineGuideRoute = '/sports-' + 'betting-line-movement-guide';
const accountConnectionRoute = '/does-inqsi-connect-to-sports' + 'books';
const comparePicksRoute = '/compare/inqsi-vs-pick-' + 'sellers';
const compareTrackingRoute = '/compare/inqsi-vs-bet-' + 'tracking-apps';

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
  '/game-leans',
  '/best-lines',
  '/parlay-scanner',
  '/ai-slip-scanner',
  '/ai-slip-builder',
  '/3-leg-parlay-guide',
  '/three-leg-cap',
  '/four-leg-guide',
  '/why-4-leg-parlays-are-risky',
  '/line-movement-guide',
  lineGuideRoute,
  '/accuracy-tracker',
  '/parlay-accuracy-tracker',
  '/accuracy-calculation',
  '/post-game-review',
  '/post-game-slip-autopsy',
  '/what-is-inqsi',
  '/how-it-works',
  '/how-inqsi-analyzes-a-slip',
  '/does-inqsi-place-bets',
  '/account-connection',
  accountConnectionRoute,
  '/my-slips-and-scores',
  '/followed-profiles',
  comparePicksRoute,
  compareTrackingRoute,
  '/compare/tools',
  '/founder-story',
  '/live-market',
  '/performance',
  '/alerts',
  movementRoute,
  '/watchlist',
  '/methodology',
  '/pricing',
  '/legal/privacy',
  '/legal/cookies',
  '/privacy-choices',
  '/data-deletion',
  '/data-export',
  '/legal/disclaimer',
  '/legal/site-terms',
  '/legal/safe-use',
  '/legal/accessibility',
  '/contact',
  '/u/inqsi-member',
  '/u/buffalo-market',
  '/u/three-leg-only'
];

export default function sitemap(): MetadataRoute.Sitemap {
  return routes.map((route) => ({
    url: `${baseUrl}${route}`,
    lastModified: new Date(),
    changeFrequency: route === '' || route === '/picks-audit' || route === '/game-leans' || route === '/live-market' ? 'daily' : 'weekly',
    priority: route === '' ? 1 : route === '/picks-audit' ? 0.95 : route === '/game-leans' ? 0.9 : route.includes('scanner') || route.includes('guide') || route.includes('accuracy') || route.includes('inqsi') ? 0.88 : 0.75
  }));
}
