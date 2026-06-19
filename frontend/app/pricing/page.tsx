import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { subscriptionPlans } from '@/lib/subscription';

export default function PricingPage() {
  return (
    <main className="shell">
      <AppHeader title="Pricing" />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">First week free</p>
        <h2>Simple launch pricing: Core or Pro.</h2>
        <p className="hero-copy">Start with seven days of free launch access, then choose the market intelligence workflow that fits how deeply you want to study each slate.</p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/register?promo=free-week" style={{ textDecoration: 'none' }}>Start Free Week</Link>
          <Link className="ghost-button large" href="/methodology" style={{ textDecoration: 'none' }}>How it works</Link>
        </div>
      </section>

      <section className="status-row pricing-grid-compact">
        {subscriptionPlans.map((plan) => (
          <article className="status-card pricing-card" key={plan.id}>
            <span>{plan.name}</span>
            <strong>{plan.price}/mo</strong>
            <p>{plan.description} Includes the first week free for new launch members.</p>
            <details className="feature-dropdown">
              <summary>View included features</summary>
              <ul>
                {plan.features.map((feature) => <li key={feature}>{feature}</li>)}
              </ul>
            </details>
            <Link className="primary-button" href={`/register?plan=${plan.id}&promo=free-week`} style={{ display: 'inline-block', marginTop: 14, textDecoration: 'none' }}>{plan.cta}</Link>
          </article>
        ))}
      </section>

      <Link className="ghost-button large" href="/register?promo=free-week" style={{ textDecoration: 'none' }}>Create account</Link>
    </main>
  );
}
