import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';

export default function PricingPage() {
  return (
    <main className="shell">
      <AppHeader title="Pricing" />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Subscription layer</p>
        <h2>Turn the intelligence terminal into a paid product.</h2>
        <p className="hero-copy">Pricing is a placeholder for launch planning. The paid value is sport coverage, game detail pages, ranked parlay structures, market movement history, and refusal logic.</p>
      </section>
      <section className="status-row">
        <article className="status-card"><span>Starter</span><strong>$19/mo</strong><p>Market board, methodology, and limited game detail pages.</p></article>
        <article className="status-card"><span>Core</span><strong>$35/mo</strong><p>Full sport boards, Top-8 parlay builds, and line movement pages.</p></article>
        <article className="status-card"><span>Pro</span><strong>$79/mo</strong><p>Multi-sport, no-overlap builds, alerts, and human-gate notes.</p></article>
        <article className="status-card"><span>Enterprise</span><strong>Custom</strong><p>Internal syndicate tooling, exports, and higher data limits.</p></article>
      </section>
      <Link className="primary-button large" href="/parlays/build" style={{ textDecoration: 'none' }}>Preview product flow</Link>
    </main>
  );
}
