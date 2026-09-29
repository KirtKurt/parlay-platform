import type { Metadata } from 'next';
import Link from 'next/link';
import { getApiSnapshot } from '@/lib/api';
import { coveragePath,familyLabel,identityFromProviderKey,isIndexableCoverage } from '@/lib/globalSports';

export const dynamic='force-dynamic';
export const metadata:Metadata={title:'Global Sports Odds, Arbitrage & Risk Coverage',description:'Browse current InQsi sports and competition coverage worldwide for sportsbook odds, arbitrage discovery and wager risk analysis.',alternates:{canonical:'/sports'}};

export default async function SportsPage(){
 const {games,apiStatus}=await getApiSnapshot('all');
 const grouped=new Map<string,{key:string;events:number;books:number;family:string;geoLabel:string;label:string}>();
 for(const g of games){const id=identityFromProviderKey(g.sport_key);const row=grouped.get(g.sport_key)||{key:g.sport_key,events:0,books:0,family:id.family,geoLabel:id.geoLabel,label:id.label};row.events++;row.books=Math.max(row.books,Number(g.bookCount||0));grouped.set(g.sport_key,row);}
 const rows=Array.from(grouped.values()).filter(r=>isIndexableCoverage({nEvents:r.events,bookCount:r.books})).sort((a,b)=>a.family.localeCompare(b.family)||a.geoLabel.localeCompare(b.geoLabel)||a.label.localeCompare(b.label));
 const families=Array.from(new Set(rows.map(r=>r.family)));
 return <main className="inqsi-shell tool-shell">
  <section className="tool-feed"><div className="tool-feed-head"><div><p>Worldwide coverage</p><h1>Global sports market coverage</h1><p>Current provider-driven competitions with sufficient live market depth. Coverage changes with the underlying sports calendar and provider inventory.</p></div><span>{apiStatus==='CONNECTED'?'Live':'Syncing'}</span></div></section>
  {families.map(f=><section className="tool-feed" key={f}><div className="tool-feed-head"><h2>{familyLabel(f)}</h2></div>{rows.filter(r=>r.family===f).map(r=><article className="tool-row" key={r.key}><div><small>{r.geoLabel}</small><strong>{r.label}</strong><p>{r.events} current event{r.events===1?'':'s'} · up to {r.books} tracked books in the current board.</p></div><Link href={coveragePath(r.key)}>View market coverage</Link></article>)}</section>)}
  {!rows.length&&<section className="tool-feed"><article className="tool-row"><div><strong>Coverage is syncing</strong><p>InQsi will list competitions here only after current provider data clears the market-depth gate.</p></div></article></section>}
 </main>;
}