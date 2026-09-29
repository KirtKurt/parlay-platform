import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getApiSnapshot } from '@/lib/api';
import { familyLabel,identityFromProviderKey,isIndexableCoverage } from '@/lib/globalSports';
export const dynamic='force-dynamic';
export async function generateMetadata({params}:{params:{family:string;competition:string}}):Promise<Metadata>{
 const {games}=await getApiSnapshot('all'); const game=games.find(g=>identityFromProviderKey(g.sport_key).slug===params.competition);
 if(!game)return {title:'Sports market coverage',robots:{index:false,follow:true}};
 const id=identityFromProviderKey(game.sport_key); const related=games.filter(g=>g.sport_key===game.sport_key);
 const books=Math.max(0,...related.map(g=>Number(g.bookCount||0))); const index=isIndexableCoverage({nEvents:related.length,bookCount:books});
 return {title:id.label+' Odds, Arbitrage & Bet Risk',description:'Current '+id.label+' sportsbook market coverage for arbitrage discovery, odds comparison, market movement and wager risk analysis when sufficient live data is available.',alternates:{canonical:'/sports/'+params.family+'/'+params.competition},robots:{index,follow:true}};
}
export default async function CoveragePage({params}:{params:{family:string;competition:string}}){
 const {games}=await getApiSnapshot('all'); const matches=games.filter(g=>identityFromProviderKey(g.sport_key).slug===params.competition); if(!matches.length)notFound();
 const id=identityFromProviderKey(matches[0].sport_key); if(params.family!==id.family.replace(/[^a-z0-9]+/g,'-'))notFound(); const books=Math.max(0,...matches.map(g=>Number(g.bookCount||0)));
 return <main className="inqsi-shell tool-shell"><section className="tool-feed">
 <div className="tool-feed-head"><div><p>{familyLabel(id.family)}</p><h1>{id.label} odds & market intelligence</h1></div></div>
 <article className="tool-row"><div><strong>{matches.length} current event{matches.length===1?'':'s'}</strong><p>{books?'Up to '+books+' tracked sportsbook quotes are present in the current board.':'Current event coverage is present; sportsbook depth is still syncing.'}</p></div></article>
 <article className="tool-row"><div><strong>Find opportunity</strong><p>ARB compares compatible sportsbook prices and calculates mathematical arbitrage when qualifying multi-book prices exist.</p></div><Link href="/arbitrage-v2">Open ARB</Link></article>
 <article className="tool-row"><div><strong>Find risk</strong><p>Slip Scanner analyzes available market movement, price quality and qualified sport-specific intelligence. It does not invent unavailable fundamentals.</p></div><Link href="/parlay-scanner">Scan a selection</Link></article>
 </section></main>;
}