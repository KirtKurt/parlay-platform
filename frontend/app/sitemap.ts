import type { MetadataRoute } from 'next';
import { getApiSnapshot } from '@/lib/api';
import { coveragePath,isIndexableCoverage } from '@/lib/globalSports';

const baseUrl=(process.env.NEXT_PUBLIC_SITE_URL || 'https://inqsi.app').replace(/\/$/,'');
const productRoutes=['','/arbitrage-v2','/arbitrage-v2/calculator','/parlay-scanner','/pricing'];
const usefulRoutes=[
 '/learn/arbitrage','/learn/arbitrage/sportsbook-odds-comparison','/learn/arbitrage/what-is-sports-arbitrage',
 '/learn/arbitrage/arbitrage-examples','/learn/arbitrage/sports-betting-line-movement',
 '/learn/arbitrage/tools/arbitrage-calculator','/learn/arbitrage/tools/surebet-calculator',
 '/learn/arbitrage/tools/implied-probability-calculator','/learn/arbitrage/tools/american-odds-calculator',
 '/learn/arbitrage/tools/sportsbook-vig-calculator','/learn/arbitrage/tools/middle-calculator',
 '/methodology','/contact','/legal/privacy','/legal/site-terms','/legal/disclaimer'
];

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
 const {games}=await getApiSnapshot('all');
 const grouped=new Map<string,{nEvents:number;bookCount:number}>();
 for(const g of games){const row=grouped.get(g.sport_key)||{nEvents:0,bookCount:0};row.nEvents++;row.bookCount=Math.max(row.bookCount,Number(g.bookCount||0));grouped.set(g.sport_key,row);}
 const coverage=Array.from(grouped.entries()).filter(([,v])=>isIndexableCoverage(v)).map(([key])=>coveragePath(key));
 return [...productRoutes,...usefulRoutes,...coverage].map((route)=>({
   url:`${baseUrl}${route}`,
   changeFrequency:route===''||route==='/arbitrage-v2'||route==='/parlay-scanner'?'daily':'weekly',
   priority:route===''?1:productRoutes.includes(route)?0.95:0.75
 }));
}
