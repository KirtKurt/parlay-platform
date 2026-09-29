import type { Metadata } from 'next';
import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { getSeoCoverage } from '@/lib/seoCoverage';
import { coveragePath,familyLabel,isIndexableCoverage } from '@/lib/globalSports';

export const dynamic='force-dynamic';
export const metadata:Metadata={
  title:'Global Sports Odds, Arbitrage & Risk Coverage',
  description:'Browse current InQsi sports and competition coverage worldwide for sportsbook odds, arbitrage discovery and wager risk analysis.',
  alternates:{canonical:'/sports'}
};

export default async function SportsPage(){
 const {sports,apiStatus}=await getSeoCoverage();
 const rows=sports.filter(r=>isIndexableCoverage({nEvents:r.eventCount,bookCount:r.bookCount}))
   .map(r=>({key:r.providerKey,events:r.eventCount,books:r.bookCount,family:r.family,geoLabel:r.geoLabel,label:r.label}))
   .sort((a,b)=>a.family.localeCompare(b.family)||a.geoLabel.localeCompare(b.geoLabel)||a.label.localeCompare(b.label));
 const families=Array.from(new Set(rows.map(r=>r.family)));
 const waiting=apiStatus!=='CONNECTED';
 return <main className="mockup-site">
  <AppHeader active="sports" apiStatus={waiting?'WAITING':'CONNECTED'} apiDetail={waiting?'Live sports provider feed is syncing':'Live market coverage connected'} />
  {waiting&&<div className="mockup-unavailable">Live odds temporarily unavailable · sports coverage will repopulate automatically when the provider feed returns</div>}
  <section className="sports-approved">
   <header className="mockup-section-head">
    <div><span className="mockup-eyebrow">Worldwide coverage</span><h1>Global sports market coverage</h1><p>InQsi follows the current provider catalog instead of a fixed league list. Competitions appear here only when current normalized market data is available.</p></div>
    <span className={'mockup-live-chip '+(waiting?'waiting':'')}>{waiting?'Syncing':'Live'}</span>
   </header>
   {families.map(f=><section className="sports-family" key={f}><h2>{familyLabel(f)}</h2><div className="sports-grid">{rows.filter(r=>r.family===f).map(r=><Link className="sports-link" href={coveragePath(r.key)} key={r.key}><small>{r.geoLabel}</small><strong>{r.label}</strong><span>{r.events} current event{r.events===1?'':'s'} · up to {r.books} books</span></Link>)}</div></section>)}
   {!rows.length&&<div className="mockup-empty"><div><b>Coverage is syncing</b><span>No sample leagues or events are substituted while the provider feed is unavailable. The worldwide catalog will return here automatically when live data resumes.</span></div></div>}
  </section>
 </main>;
}
