import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';

export default function CheckoutSuccessPage() {
  return (
    <main className="shell">
      <AppHeader title="Membership active" />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Checkout success</p>
        <h2>Welcome to InQsi.</h2>
        <p className="hero-copy">This success state is ready for your billing provider confirmation. In production, member access should activate only after the provider confirms the subscription.</p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/sports" style={{ textDecoration: 'none' }}>Open sports board</Link>
          <Link className="ghost-button large" href="/account" style={{ textDecoration: 'none' }}>View account</Link>
        </div>
      </section>
      <section className="status-row">
        <article className="status-card"><span>Subscription</span><strong>Active demo</strong><p>Production should read this from billing confirmations.</p></article>
        <article className="status-card"><span>Access</span><strong>Full Access</strong><p>Full sport boards and Top-8 parlay rankings.</p></article>
        <article className="status-card"><span>Next step</span><strong>Set alerts</strong><p>Choose steam, resistance, chaos, and anomaly alerts.</p></article>
        <article className="status-card"><span>Billing</span><strong>Monthly</strong><p>Recurring subscription workflow is staged.</p></article>
      </section>
    </main>
  );
}
