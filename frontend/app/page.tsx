import type { Metadata } from 'next';
import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';

export const metadata:Metadata={title:'Sports Arbitrage & Bet Risk Intelligence',description:'Find sports arbitrage opportunities and analyze wager risk with sportsbook pricing, market movement and InQsi intelligence.',alternates:{canonical:'/'}};

export default function Home(){
 return <main className="inqsi-shell mock-home">
  <AppHeader eyebrow="InQsi" title="Find Opportunity. Find Risk." />
  <section className="mock-home-hero">
   <div className="mock-home-copy"><span className="mock-live-dot">SPORTS MARKET INTELLIGENCE</span><h1>Find the edge.<br/><em>See the risk.</em></h1><p>One focused workspace for mathematical arbitrage and pre-bet risk analysis. No public picks feed. No hype. Just market intelligence built to help you see what the price is telling you.</p><div className="mock-home-actions"><Link className="mock-cta primary" href="/arbitrage-v2">Find Opportunities →</Link><Link className="mock-cta" href="/parlay-scanner">Scan My Picks</Link></div></div>
   <div className="mock-market-card"><header><span>LIVE MARKET</span><b>Feed status</b></header><div className="mock-market-body"><i>ARB</i><strong>Opportunity scanner wired</strong><p>Sportsbook feeds are temporarily unavailable. InQsi will populate this board automatically when the provider resumes.</p><div><span>Books</span><b>—</b><span>Opportunities</span><b>—</b></div></div><footer><span className="mock-status-pulse"/> Awaiting sportsbook feed</footer></div>
  </section>
  <section className="mock-product-grid">
   <Link href="/arbitrage-v2" className="mock-product-card"><div className="mock-product-icon">↗</div><small>FIND OPPORTUNITY</small><h2>ARB</h2><p>Compare compatible prices across sportsbooks, surface mathematical arbitrage, and calculate exact stake allocation for any bankroll.</p><span>Open ARB →</span></Link>
   <Link href="/parlay-scanner" className="mock-product-card risk"><div className="mock-product-icon">⌁</div><small>FIND RISK</small><h2>Slip Scanner</h2><p>Choose your sport, game and selection. InQsi checks market movement, price quality and qualified intelligence before you place the bet.</p><span>Scan a Pick →</span></Link>
  </section>
  <section className="mock-proof"><div><small>01</small><strong>Choose a market</strong><p>Browse the current provider-driven sports catalog.</p></div><div><small>02</small><strong>Compare or scan</strong><p>Use ARB for opportunity or Slip Scanner for risk.</p></div><div><small>03</small><strong>You decide</strong><p>InQsi shows the information. The betting decision remains yours.</p></div></section>
 </main>;
}