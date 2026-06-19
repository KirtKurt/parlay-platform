import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { subscriptionPlans } from '@/lib/subscription';

export default function PricingPage() {
  return (
    <main className="shell">
      <AppHeader title="Pricing" />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">First week free</p>
        <h2>Start with seven days of free launch access.</h2>
        <p className="hero-copy">Explore the sports lobby, methodology, market board preview, and account flow before choosing the plan that fits your research workflow.</p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/register?promo=free-week" style={{ textDecoration: 'none' }}>Start Free Week</Link>
          <Link className="ghost-button large" href="/methodology" style={{ textDecoration: 'none' }}>How it works</Link>
        </div>
      </section>
      <section className="status-row">
        {subscriptionPlans.map((plan) => (
          <article className="status-card" key={plan.id}>
            <span>{plan.name}</span>
            <strong>{plan.price}/mo</strong>
            <p>{plan.description} Includes the first week free for new launch members.</p>
            <Link className="primary-button" href={`/register?plan=${plan.id}&promo=free-week`} style={{ display: 'inline-block', marginTop: 14, textDecoration: 'none' }}>{plan.cta}</Link>
          </article>
        ))}
        <article className="status-card"><span>Enterprise</span><strong>Custom</strong><p>Internal syndicate tooling, exports, and higher data limits.</p></article>
      </section>
      <Link className="ghost-button large" href="/register?promo=free-week" style={{ textDecoration: 'none' }}>Create account</Link>
    </main>
  );
}
