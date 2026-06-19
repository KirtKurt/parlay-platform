import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { subscriptionPlans } from '@/lib/subscription';

export default function PricingPage() {
  return (
    <main className="shell">
      <AppHeader title="Pricing" />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Monthly membership</p>
        <h2>Choose the plan that matches how serious the customer is.</h2>
        <p className="hero-copy">The Core $35/month plan is the primary subscription. The signup flow now moves from account registration to secure hosted checkout.</p>
      </section>
      <section className="status-row">
        {subscriptionPlans.map((plan) => (
          <article className="status-card" key={plan.id}>
            <span>{plan.name}</span>
            <strong>{plan.price}/mo</strong>
            <p>{plan.description}</p>
            <Link className="primary-button" href={`/register?plan=${plan.id}`} style={{ display: 'inline-block', marginTop: 14, textDecoration: 'none' }}>{plan.cta}</Link>
          </article>
        ))}
        <article className="status-card"><span>Enterprise</span><strong>Custom</strong><p>Internal syndicate tooling, exports, and higher data limits.</p></article>
      </section>
      <Link className="ghost-button large" href="/register" style={{ textDecoration: 'none' }}>Create account</Link>
    </main>
  );
}
