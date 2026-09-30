import type { Metadata } from 'next';
import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { getSeoCoverage } from '@/lib/seoCoverage';
import { findGame } from '@/lib/findGame';
import { formatAmericanOdds,formatKickoff,gamePath,impliedPercent } from '@/lib/kickoff';
import { coveragePath,identityFromProviderKey,isIndexableCoverage } from '@/lib/globalSports';

export const dynamic='force-dynamic';
function eventState(start?:string){const stamp=Date.parse(String(start||''));if(!Number.isFinite(stamp))return {stale:true};return {stale:Date.now()-stamp>36*60*60*1000};}

export async function generateMetadata({params}:{params:{gameId:string}}):Promise<Metadata>{
 const {events}=await getSeoCoverage();const game=findGame(events,params.gameId);
 if(!game)return {title:'Waiting',robots:{index:false,follow:true}};
 const state=eventState(game.start),index=!state.stale&&isIndexableCoverage({nEvents:1,bookCount:Number(game.bookCount||0)});
 const title=(game.away_team&&game.home_team)?game.away_team+' vs '+game.home_team+' Odds & Market Risk':game.matchup+' Odds & Market Risk';
 return {title,description:'Current sportsbook pricing, implied probability, market context, arbitrage access and wager-risk tools for '+(game.matchup||'this sporting event')+'.',alternates:{canonical:gamePath(game)},robots:{index,follow:true}};
}

export default async function GameDetailPage({params}:{params:{gameId:string}}){
 const {events,apiStatus,apiDetail}=await getSeoCoverage();const game=findGame(events,params.gameId);
 if(!game){
  return <main className="mockup-site">
   <AppHeader active="sports" apiStatus={apiStatus==='CONNECTED'?'CONNECTED':'WAITING'} apiDetail={apiDetail}/>
   <section className="market-detail-page">
    <div className="market-breadcrumb"><Link href="/sports">Sports</Link> / Event</div>
    <article className="market-detail-card">
     <header className="market-detail-head"><span className="mockup-eyebrow">Live board</span><h1>Waiting</h1><p>This event is not on the current live board.</p></header>
     <div className="market-detail-row"><div><small>Data integrity</small><p>{apiDetail||'InQsi uses current normalized market data and does not invent unavailable prices, opportunities or fundamentals.'}</p></div></div>
     <div className="market-detail-row"><div><small>Find opportunity</small><strong>ARB</strong></div><Link href="/arbitrage-v2">Open ARB →</Link></div>
     <div className="market-detail-row"><div><small>Find risk</small><strong>Slip Scanner</strong></div><Link href="/parlay-scanner">Scan a selection →</Link></div>
    </article>
   </section>
  </main>;
 }
 const id=identityFromProviderKey(game.sport_key),start=formatKickoff(game.start),state=eventState(game.start);
 const favOdds=game.favoriteMl,dogOdds=game.underdogMl,implied=impliedPercent(favOdds);
 const siteUrl=(process.env.NEXT_PUBLIC_SITE_URL||'https://inqsi.app').replace(/\/$/,'');
 const eventJsonLd={'@context':'https://schema.org','@type':'SportsEvent',name:game.matchup||game.away_team+' vs '+game.home_team,startDate:game.start,eventStatus:'https://schema.org/EventScheduled',url:siteUrl+gamePath(game),homeTeam:game.home_team?{'@type':'SportsTeam',name:game.home_team}:undefined,awayTeam:game.away_team?{'@type':'SportsTeam',name:game.away_team}:undefined};
 const crumbJsonLd={'@context':'https://schema.org','@type':'BreadcrumbList',itemListElement:[{'@type':'ListItem',position:1,name:'Sports',item:siteUrl+'/sports'},{'@type':'ListItem',position:2,name:id.label,item:siteUrl+coveragePath(game.sport_key)},{'@type':'ListItem',position:3,name:game.matchup||'Event',item:siteUrl+gamePath(game)}]};
 return <main className="mockup-site">
  <script type="application/ld+json" dangerouslySetInnerHTML={{__html:JSON.stringify(eventJsonLd)}}/><script type="application/ld+json" dangerouslySetInnerHTML={{__html:JSON.stringify(crumbJsonLd)}}/>
  <AppHeader active="sports" apiStatus={apiStatus==='CONNECTED'?'CONNECTED':'WAITING'} apiDetail={apiDetail}/>
  <section className="market-detail-page">
   <div className="market-breadcrumb"><Link href="/sports">Sports</Link> / <Link href={coveragePath(game.sport_key)}>{id.label}</Link> / Event</div>
   <article className="market-detail-card">
    <header className="market-detail-head"><span className="mockup-eyebrow">{id.label}</span><h1>{game.matchup||'Sports event market'}</h1><p>{start} · {apiStatus==='CONNECTED'?'current market board':'market data syncing'}</p></header>
    {state.stale&&<div className="market-detail-row"><div><small>Search status</small><strong>Event completed or stale</strong><p>This event is retained for navigation context but excluded from indexing after the live market window.</p></div></div>}
    <div className="market-detail-row"><div><small>Current price context</small><strong>{game.favorite?game.favorite+' '+(formatAmericanOdds(favOdds)||'price syncing')+(implied?' · '+implied+'% raw implied probability':''):'Favorite price syncing'}</strong><p>{game.underdog?game.underdog+' '+(formatAmericanOdds(dogOdds)||'price syncing'):'Opposing price syncing'}</p></div></div>
    <div className="market-detail-row"><div><small>Market depth</small><strong>{game.bookCount?game.bookCount+' sportsbook quotes represented':'Sportsbook depth syncing'}</strong><p>{game.spread&&game.spread!=='Waiting'?'Spread: '+game.spread+'. ':''}{game.total&&game.total!=='Waiting'?'Total: '+game.total+'.':''}</p></div></div>
    <div className="market-detail-row"><div><small>Find opportunity</small><strong>ARB</strong><p>Compare compatible sportsbook prices and check whether a mathematical arbitrage opportunity exists.</p></div><Link href="/arbitrage-v2">Open ARB →</Link></div>
    <div className="market-detail-row"><div><small>Find risk</small><strong>Slip Scanner</strong><p>Analyze available market movement, price quality and qualified sport-specific intelligence for your selection.</p></div><Link href="/parlay-scanner">Scan a selection →</Link></div>
    <div className="market-detail-row"><div><small>Data integrity</small><p>{apiDetail||'InQsi uses current normalized market data and does not invent unavailable prices, opportunities or fundamentals.'}</p></div></div>
   </article>
  </section>
 </main>;
}
