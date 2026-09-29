import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getSeoCoverage } from '@/lib/seoCoverage';
import { findGame } from '@/lib/findGame';
import { formatAmericanOdds,formatKickoff,gamePath,impliedPercent } from '@/lib/kickoff';
import { coveragePath,identityFromProviderKey,isIndexableCoverage } from '@/lib/globalSports';

export const dynamic='force-dynamic';

function eventState(start?:string){const stamp=Date.parse(String(start||''));if(!Number.isFinite(stamp))return {future:false,stale:true};const age=Date.now()-stamp;return {future:age<0,stale:age>36*60*60*1000};}

export async function generateMetadata({params}:{params:{gameId:string}}):Promise<Metadata>{
 const {events}=await getSeoCoverage();const game=findGame(events,params.gameId);
 if(!game)return {title:'Sports event market',robots:{index:false,follow:true}};
 const state=eventState(game.start);const index=!state.stale&&isIndexableCoverage({nEvents:1,bookCount:Number(game.bookCount||0)});
 const title=(game.away_team&&game.home_team)?game.away_team+' vs '+game.home_team+' Odds & Market Risk':game.matchup+' Odds & Market Risk';
 return {title,description:'Current sportsbook pricing, implied probability, market context, arbitrage access and wager-risk tools for '+(game.matchup||'this sporting event')+'.',alternates:{canonical:gamePath(game)},robots:{index,follow:true}};
}

export default async function GameDetailPage({params}:{params:{gameId:string}}){
 const {events,apiStatus,apiDetail}=await getSeoCoverage();const game=findGame(events,params.gameId);if(!game)notFound();
 const id=identityFromProviderKey(game.sport_key);const start=formatKickoff(game.start);const state=eventState(game.start);
 const favOdds=game.favoriteMl;const dogOdds=game.underdogMl;const implied=impliedPercent(favOdds);
 const siteUrl=(process.env.NEXT_PUBLIC_SITE_URL||'https://inqsi.app').replace(/\/$/,'');
 const eventJsonLd={'@context':'https://schema.org','@type':'SportsEvent',name:game.matchup||game.away_team+' vs '+game.home_team,startDate:game.start,eventStatus:'https://schema.org/EventScheduled',url:siteUrl+gamePath(game),homeTeam:game.home_team?{'@type':'SportsTeam',name:game.home_team}:undefined,awayTeam:game.away_team?{'@type':'SportsTeam',name:game.away_team}:undefined};
 const crumbJsonLd={'@context':'https://schema.org','@type':'BreadcrumbList',itemListElement:[{'@type':'ListItem',position:1,name:'Sports',item:siteUrl+'/sports'},{'@type':'ListItem',position:2,name:id.label,item:siteUrl+coveragePath(game.sport_key)},{'@type':'ListItem',position:3,name:game.matchup||'Event',item:siteUrl+gamePath(game)}]};
 return <main className="inqsi-shell tool-shell game-sheet">
  <script type="application/ld+json" dangerouslySetInnerHTML={{__html:JSON.stringify(eventJsonLd)}}/><script type="application/ld+json" dangerouslySetInnerHTML={{__html:JSON.stringify(crumbJsonLd)}}/>
  <section className="tool-feed">
   <div className="tool-feed-head"><div><p><Link href="/sports">Sports</Link> / <Link href={coveragePath(game.sport_key)}>{id.label}</Link></p><h1>{game.matchup||'Sports event market'}</h1><p>{start} · {apiStatus==='CONNECTED'?'Current market board':'Market data syncing'}</p></div></div>
   {state.stale&&<article className="tool-row"><div><strong>Event completed or stale</strong><p>This event is retained for navigation context but is excluded from search indexing after the live market window.</p></div></article>}
   <article className="tool-row"><div><strong>Current price context</strong><p>{game.favorite?game.favorite+' '+(formatAmericanOdds(favOdds)||'price syncing')+(implied?' · '+implied+'% raw implied probability':''):'Favorite price syncing'}{game.underdog?' · '+game.underdog+' '+(formatAmericanOdds(dogOdds)||'price syncing'):''}</p></div></article>
   <article className="tool-row"><div><strong>Market depth</strong><p>{game.bookCount?game.bookCount+' sportsbook quotes represented in the current board.':'Sportsbook depth is still syncing.'} {game.spread&&game.spread!=='Waiting'?'Spread: '+game.spread+'. ':''}{game.total&&game.total!=='Waiting'?'Total: '+game.total+'.':''}</p></div></article>
   <article className="tool-row"><div><strong>Find opportunity</strong><p>Compare compatible sportsbook prices and check whether a mathematical arbitrage opportunity exists.</p></div><Link href="/arbitrage-v2">Open ARB</Link></article>
   <article className="tool-row"><div><strong>Find risk</strong><p>Analyze available market movement, price quality and qualified sport-specific intelligence for your selection.</p></div><Link href="/parlay-scanner">Scan a selection</Link></article>
   <article className="tool-row"><div><small>Data integrity</small><p>{apiDetail||'InQsi uses current normalized market data and does not invent unavailable prices, opportunities or fundamentals.'}</p></div></article>
  </section>
 </main>;
}