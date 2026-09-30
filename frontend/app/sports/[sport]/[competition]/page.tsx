import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { AppHeader } from '@/components/AppHeader';
import { getSeoCoverage } from '@/lib/seoCoverage';
import { familyLabel,identityFromProviderKey,isIndexableCoverage } from '@/lib/globalSports';
import { formatKickoff,gamePath } from '@/lib/kickoff';

export const dynamic='force-dynamic';

export async function generateMetadata({params}:{params:{sport:string;competition:string}}):Promise<Metadata>{
 const {events}=await getSeoCoverage();
 const game=events.find(g=>identityFromProviderKey(g.sport_key).slug===params.competition);
 if(!game)return {title:'Sports market coverage',robots:{index:false,follow:true}};
 const id=identityFromProviderKey(game.sport_key),related=events.filter(g=>g.sport_key===game.sport_key);
 const books=Math.max(0,...related.map(g=>Number(g.bookCount||0))),index=isIndexableCoverage({nEvents:related.length,bookCount:books});
 return {title:id.label+' Odds, Arbitrage & Bet Risk',description:'Current '+id.label+' sportsbook market coverage for arbitrage discovery, odds comparison, market movement and wager risk analysis when sufficient live data is available.',alternates:{canonical:'/sports/'+params.sport+'/'+params.competition},robots:{index,follow:true}};
}

export default async function CoveragePage({params}:{params:{sport:string;competition:string}}){
 const {events,apiStatus}=await getSeoCoverage();
 const matches=events.filter(g=>identityFromProviderKey(g.sport_key).slug===params.competition);
 if(!matches.length)notFound();
 const id=identityFromProviderKey(matches[0].sport_key);
 if(params.sport!==id.family.replace(/[^a-z0-9]+/g,'-'))notFound();
 const books=Math.max(0,...matches.map(g=>Number(g.bookCount||0)));
 return <main className="mockup-site">
  <AppHeader active="sports" apiStatus={apiStatus==='CONNECTED'?'CONNECTED':'WAITING'}/>
  <section className="market-detail-page">
   <div className="market-breadcrumb"><Link href="/sports">Sports</Link> / {familyLabel(id.family)} / {id.geoLabel}</div>
   <article className="market-detail-card">
    <header className="market-detail-head"><span className="mockup-eyebrow">{familyLabel(id.family)} · {id.geoLabel}</span><h1>{id.label}</h1><p>{matches.length} current event{matches.length===1?'':'s'} · {books?'up to '+books+' tracked books':'market depth syncing'}</p></header>
    {matches.slice(0,20).map(game=><div className="market-detail-row" key={game.game_id}><div><small>{formatKickoff(game.start)}</small><strong>{game.matchup}</strong><p>{game.bookCount?game.bookCount+' sportsbooks currently represented.':'Sportsbook depth is syncing.'}</p></div><Link href={gamePath(game)}>View event →</Link></div>)}
    <div className="market-detail-row"><div><small>Find opportunity</small><strong>ARB</strong><p>Compare compatible sportsbook prices and calculate mathematical arbitrage when qualifying multi-book prices exist.</p></div><Link href="/arbitrage-v2">Open ARB →</Link></div>
    <div className="market-detail-row"><div><small>Find risk</small><strong>Slip Scanner</strong><p>Analyze price, market movement and qualified sport-specific intelligence for a selection.</p></div><Link href="/parlay-scanner">Scan a selection →</Link></div>
   </article>
  </section>
 </main>;
}