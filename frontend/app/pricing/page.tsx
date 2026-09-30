import type { Metadata } from 'next';
import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';

export const metadata: Metadata = {
  title: 'Pricing',
  description: 'InQsi pricing for ARB and Slip Scanner.',
  alternates: { canonical: '/pricing' }
};

export default function PricingPage() {
  return (
    <main className="mockup-site">
      <AppHeader active="pricing" />
      <section className="pricing-approved">
        <article className="pricing-card">
          <span className="mockup-eyebrow">Simple membership</span>
          <h1>One package.<br/><span>Full access.</span></h1>
          <p>Use InQsi ARB to find mathematical pricing opportunities and Slip Scanner to inspect the risk around selections you are considering.</p>
          <div className="pricing-price"><strong>$38</strong><span>/ month</span></div>
          <div className="pricing-features">
            <div><b>ARB</b><span>Live arbitrage discovery, stake allocation, sportsbook price comparison and market detail when qualifying live prices exist.</span></div>
            <div><b>Slip Scanner</b><span>Structured sport → game → selection workflow with concise risk analysis and deeper context where qualified intelligence exists.</span></div>
            <div><b>Worldwide coverage</b><span>Provider-driven sports and competition support rather than a fixed list of leagues.</span></div>
            <div><b>One account</b><span>Access the public InQsi product suite from one membership.</span></div>
          </div>
          <Link className="mockup-cta" href="/register">Start membership →</Link>
          <p className="pricing-note">InQsi does not place wagers or guarantee outcomes. Live features depend on current sportsbook/provider availability.</p>
        </article>
      </section>
    </main>
  );
}
