import type { Metadata } from 'next';
import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';

export const metadata: Metadata = {
  title:'Sports Arbitrage & Bet Risk Intelligence',
  description:'Find sports arbitrage opportunities and analyze wager risk with global sportsbook odds, market movement, price comparison and InQsi sports intelligence.',
  alternates:{canonical:'/'}
};

export default function Home(){
 return <main className="inqsi-shell tool-shell">
  <AppHeader eyebrow="InQsi" title="Find Opportunity. Find Risk." />
  <section className="tool-feed" id="main-content">
   <div className="tool-feed-head"><h1>Global sports market intelligence</h1></div>
   <article className="tool-row">
    <div><small>ARB</small><strong>Find arbitrage opportunities</strong><p>Compare compatible sportsbook prices, identify mathematical arbitrage opportunities and calculate stake allocation across supported sports and competitions worldwide.</p></div>
    <Link href="/arbitrage-v2">Open ARB</Link>
   </article>
   <article className="tool-row">
    <div><small>SLIP SCANNER</small><strong>Find the risk before you bet</strong><p>Pick a sport, event and selection. InQsi analyzes available market movement, price quality and qualified sport-specific intelligence without publishing a public picks feed.</p></div>
    <Link href="/parlay-scanner">Scan a pick</Link>
   </article>
  </section>
  <section className="tool-feed">
   <div className="tool-feed-head"><h2>Worldwide coverage, driven by current data</h2></div>
   <article className="tool-row"><div><strong>Sport → country or region → competition → event → market</strong><p>InQsi is designed around the current normalized catalog supplied by authorized data providers rather than a fixed U.S.-league list. Capabilities are shown only when the required data exists.</p></div></article>
  </section>
  <section className="tool-feed">
   <div className="tool-feed-head"><h2>Free market tools</h2><Link href="/learn/arbitrage">Learn arbitrage</Link></div>
   <article className="tool-row"><div><strong>Arbitrage, implied probability, vig and odds tools</strong><p>Use practical calculators and guides to understand sportsbook pricing, line movement and arbitrage mathematics.</p></div><Link href="/arbitrage-v2/calculator">Open calculator</Link></article>
  </section>
 </main>;
}
