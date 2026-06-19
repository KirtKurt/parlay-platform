import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { featureComparison, subscriptionPlans } from '@/lib/subscription';

export default function PricingPage() {
  return (
    <main className="shell">
      <AppHeader title="Pricing" />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">First week free</p>
        <h2>Choose your Silvers Syndicate access level.</h2>
        <p className="hero-copy">Start with seven days of free launch access. Core is built for daily slate research. Pro is built for deeper no-overlap construction, watchlists, and advanced volatility review.</p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/register?promo=free-week" style={{ textDecoration: 'none' }}>Start Free Week</Link>
          <Link className="ghost-button large" href="/methodology" style={{ textDecoration: 'none' }}>How it works</Link>
        </div>
      </section>

      <section className="status-row" style={{ gridTemplateColumns: 'repeat(2, minmax(0, 1fr))' }}>
        {subscriptionPlans.map((plan) => (
          <article className="status-card" key={plan.id}>
            <span>{plan.name}</span>
            <strong>{plan.price}/mo</strong>
            <p>{plan.description} Includes the first week free for new launch members.</p>
            <details style={{ marginTop: 16, borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: 14 }}>
              <summary style={{ cursor: 'pointer', color: '#20f29f', fontWeight: 900 }}>View included features</summary>
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
          <summary style={{ cursor: 'pointer', color: '#20f29f', fontWeight: 900, fontSize: '1rem' }}>Compare Core vs Pro features</summary>
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

      <Link className="ghost-button large" href="/register?promo=free-week" style={{ textDecoration: 'none', marginTop: 22 }}>Create account</Link>
    </main>
  );
}
