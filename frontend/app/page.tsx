import type { Metadata } from 'next';
import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';

export const metadata: Metadata = {
  title: 'Sports Arbitrage & Bet Risk Intelligence',
  description: 'Find sports arbitrage opportunities and analyze wager risk with global sportsbook odds, market movement, price comparison and InQsi sports intelligence.',
  alternates: { canonical: '/' }
};

export default function Home() {
  return (
    <main className="mockup-site">
      <AppHeader active="home" />
      <section className="mockup-home">
        <div className="mockup-home-hero">
          <article className="mockup-hero-card">
            <span className="mockup-eyebrow">Sports market intelligence</span>
            <h1>FIND OPPORTUNITY.<br/><span>FIND RISK.</span></h1>
            <p>InQsi compares sportsbook markets for mathematical arbitrage and helps you inspect the risk around the selections you are considering before you place a bet.</p>
            <div className="mockup-hero-actions">
              <Link className="mockup-cta" href="/arbitrage-v2">Open ARB →</Link>
              <Link className="mockup-cta secondary" href="/parlay-scanner">Open Slip Scanner</Link>
            </div>
          </article>
          <div className="mockup-products">
            <article className="mockup-product-card">
              <small>ARB</small>
              <h2>Find Opportunity</h2>
              <p>Compare compatible sportsbook prices, calculate the exact stake split, and surface mathematical arbitrage when live market data confirms it.</p>
              <Link href="/arbitrage-v2">View opportunities →</Link>
            </article>
            <article className="mockup-product-card">
              <small>SLIP SCANNER</small>
              <h2>Find Risk</h2>
              <p>Pick a sport, game, market and side. InQsi reviews available market movement, price quality and qualified sport-specific intelligence.</p>
              <Link href="/parlay-scanner">Scan a selection →</Link>
            </article>
          </div>
        </div>
        <section className="mockup-market-strip" aria-label="InQsi product principles">
          <article className="mockup-market-card"><b>Worldwide coverage</b><span>Provider-driven sports and competitions, not a fixed U.S.-league list.</span></article>
          <article className="mockup-market-card"><b>Real data only</b><span>No fabricated prices, opportunities, picks or market status when a provider is unavailable.</span></article>
          <article className="mockup-market-card"><b>You decide</b><span>InQsi surfaces opportunity and risk; it does not place wagers or promise outcomes.</span></article>
        </section>
      </section>
    </main>
  );
}
