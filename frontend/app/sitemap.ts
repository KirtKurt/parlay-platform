import type { MetadataRoute } from 'next';

const baseUrl=(process.env.NEXT_PUBLIC_SITE_URL || 'https://inqsi.app').replace(/\/$/,'');
const productRoutes=['','/sports','/arbitrage-v2','/arbitrage-v2/calculator','/parlay-scanner','/pricing'];
const usefulRoutes=[
 '/learn/arbitrage','/learn/arbitrage/sportsbook-odds-comparison','/learn/arbitrage/what-is-sports-arbitrage',
 '/learn/arbitrage/arbitrage-examples','/learn/arbitrage/sports-betting-line-movement',
 '/learn/arbitrage/tools/arbitrage-calculator','/learn/arbitrage/tools/surebet-calculator',
 '/learn/arbitrage/tools/implied-probability-calculator','/learn/arbitrage/tools/american-odds-calculator',
 '/learn/arbitrage/tools/sportsbook-vig-calculator','/learn/arbitrage/tools/middle-calculator',
 '/methodology','/contact','/legal/privacy','/legal/site-terms','/legal/disclaimer'
];

export default function sitemap():MetadataRoute.Sitemap{
 return [...productRoutes,...usefulRoutes].map(route=>({
  url:baseUrl+route,
  changeFrequency:route===''||route==='/sports'||route==='/arbitrage-v2'||route==='/parlay-scanner'?'daily':'weekly',
  priority:route===''?1:productRoutes.includes(route)?0.95:0.75
 }));
}
