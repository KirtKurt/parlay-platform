import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { SportHeroPanel, SportIconStrip, TeamJerseyBadge } from '@/components/SportVisuals';
import { featureComparison, subscriptionPlans } from '@/lib/subscription';

export default function PricingPage() {
  return (
    <main className="shell">
      <AppHeader title="Pricing" />
      <section className="sport-hero-grid">
        <div className="hero-card glass-card" style={{ minHeight: 0 }}>
          <p className="eyebrow blue">First week free</p>
          <h2>Start with a free week. Keep the plan that fits how you follow sports.</h2>
          <p className="hero-copy">
            Core is built for everyday slate research. Pro is for members who want more depth, more sports coverage, stronger no-overlap tools, and a closer look at volatility. No complicated menu. Just two clear options.
          </p>
          <div className="team-badge-row" style={{ marginTop: 16 }}>
            <TeamJerseyBadge abbr="PRO" tone="gold" number="79" />
            <b>+</b>
            <TeamJerseyBadge abbr="CORE" tone="blue" number="35" />
            <span style={{ color: '#96a4bd', fontSize: '.85rem' }}>Plan markers use the same jersey badge system.</span>
          </div>
          <div className="hero-actions">
            <Link className="primary-button large" href="/register?promo=free-week&plan=pro" style={{ textDecoration: 'none' }}>Start Free Week</Link>
            <Link className="ghost-button large" href="/methodology" style={{ textDecoration: 'none' }}>How it works</Link>
          </div>
        </div>
        <SportHeroPanel sportSlug="nba" title="Plans should feel like the product." copy="Pricing now keeps the equipment icon strip and badge language so the site feels consistent from first click to signup." />
      </section>

      <SportIconStrip compact />

      <section className="status-row" style={{ gridTemplateColumns: 'repeat(2, minmax(0, 1fr))' }}>
        {subscriptionPlans.map((plan) => (
          <article className="status-card" key={plan.id}>
            <TeamJerseyBadge abbr={plan.name.toUpperCase()} tone={plan.id === 'pro' ? 'gold' : 'blue'} number={plan.price.replace('$', '')} />
            <span>{plan.name}</span>
            <strong>{plan.price}/mo</strong>
            <p>{plan.description} New members get the first week free.</p>
            <details style={{ marginTop: 16, borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: 14 }}>
              <summary style={{ cursor: 'pointer', color: '#20f29f', fontWeight: 900 }}>What’s included</summary>
              <ul style={{ margin: '14px 0 0', paddingLeft: 18, color: '#96a4bd', lineHeight: 1.7 }}>
                {plan.features.map((feature) => <li key={feature}>{feature}</li>)}
              </ul>
            </details>
            <Link className="primary-button" href={`/register?plan=${plan.id}&promo=free-week`} style={{ display: 'inline-block', marginTop: 14, textDecoration: 'none' }}>{plan.cta}</Link>
          </article>
        ))}
      </section>

      <section className="glass-card" style={{ marginTop: 22, padding: 22 }}>
        <details open>
          <summary style={{ cursor: 'pointer', color: '#20f29f', fontWeight: 900, fontSize: '1rem' }}>Compare Core and Pro</summary>
          <p style={{ color: '#96a4bd', marginTop: 12, maxWidth: 760 }}>
            Core gives you the main market board and daily research flow. Pro is for members who want the deeper workflow: more alerts, more sports, stronger build tools, and better review support before locking in a slate.
          </p>
          <div style={{ overflowX: 'auto', marginTop: 18 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 680 }}>
              <thead>
                <tr>
                  <th style={{ textAlign: 'left', padding: '12px 10px', color: '#e8edf7', borderBottom: '1px solid rgba(255,255,255,0.12)' }}>Feature</th>
                  <th style={{ textAlign: 'left', padding: '12px 10px', color: '#e8edf7', borderBottom: '1px solid rgba(255,255,255,0.12)' }}>Core</th>
                  <th style={{ textAlign: 'left', padding: '12px 10px', color: '#e8edf7', borderBottom: '1px solid rgba(255,255,255,0.12)' }}>Pro</th>
                </tr>
              </thead>
              <tbody>
                {featureComparison.map((row) => (
                  <tr key={row.feature}>
                    <td style={{ padding: '12px 10px', color: '#f4f7ff', fontWeight: 800, borderBottom: '1px solid rgba(255,255,255,0.08)' }}>{row.feature}</td>
                    <td style={{ padding: '12px 10px', color: '#96a4bd', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>{row.core}</td>
                    <td style={{ padding: '12px 10px', color: '#96a4bd', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>{row.pro}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </section>

      <Link className="ghost-button large" href="/register?promo=free-week&plan=pro" style={{ textDecoration: 'none', marginTop: 22 }}>Create account</Link>
    </main>
  );
}
