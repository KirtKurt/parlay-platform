import type { MetadataRoute } from 'next';
import { getSeoCoverage } from '@/lib/seoCoverage';
import { coveragePath,isIndexableCoverage } from '@/lib/globalSports';
import { gamePath } from '@/lib/kickoff';

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

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
 const {events,sports}=await getSeoCoverage();
 const coverage=sports.filter(s=>isIndexableCoverage({nEvents:s.eventCount,bookCount:s.bookCount})).map(s=>coveragePath(s.providerKey));
 const now=Date.now();
 const eventRoutes=events.filter(g=>{const stamp=Date.parse(String(g.start||''));return Number.isFinite(stamp)&&stamp>now-36*60*60*1000&&isIndexableCoverage({nEvents:1,bookCount:Number(g.bookCount||0)});}).map(g=>gamePath(g));
 return [...productRoutes,...usefulRoutes,...coverage,...eventRoutes].map((route)=>({
   url:`${baseUrl}${route}`,
   changeFrequency:route===''||route==='/arbitrage-v2'||route==='/parlay-scanner'?'daily':'weekly',
   priority:route===''?1:productRoutes.includes(route)?0.95:0.75
 }));
}
