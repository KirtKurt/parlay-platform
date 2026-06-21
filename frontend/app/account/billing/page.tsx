import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { TeamJerseyBadge } from '@/components/SportVisuals';

export const metadata = {
  title: 'Billing & Payment Portal',
  description: 'Manage InQsi billing and payment methods through the secure payment provider portal.',
  alternates: { canonical: '/account/billing' }
};

export default function BillingPortalPage() {
  return (
    <main className="shell">
      <AppHeader title="Billing & Payment Portal" />

      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Account billing</p>
        <div className="team-badge-row" style={{ marginTop: 10 }}>
          <TeamJerseyBadge abbr="PAY" tone="gold" number="0" />
          <TeamJerseyBadge abbr="CARD" tone="blue" number="0" />
        </div>
        <h2>Manage billing without InQsi storing card data.</h2>
        <p className="hero-copy">Members should be able to update payment methods, view invoices, manage renewal, and change subscription settings through the secure payment provider portal.</p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/account" style={{ textDecoration: 'none' }}>Back to Account</Link>
          <Link className="ghost-button large" href="/pricing" style={{ textDecoration: 'none' }}>View Pricing</Link>
        </div>
      </section>

      <section className="content-grid">
        <article className="panel">
          <div className="panel-header compact">
            <div>
              <p className="eyebrow blue">Provider-hosted portal</p>
              <h3>What customers should manage here</h3>
            </div>
          </div>
          <div className="game-list">
            <article className="game-card"><div className="game-topline"><span className="league-chip">Card</span><span>Secure update</span></div><h4>Update payment method</h4><p className="movement">Change or replace the card through the payment provider portal.</p></article>
            <article className="game-card"><div className="game-topline"><span className="league-chip">Invoices</span><span>Billing history</span></div><h4>View invoices and receipts</h4><p className="movement">Customers should be able to review payment history without contacting support.</p></article>
            <article className="game-card"><div className="game-topline"><span className="league-chip">Plan</span><span>Subscription</span></div><h4>Manage renewal or cancellation</h4><p className="movement">Subscription changes should happen inside the provider-hosted flow.</p></article>
          </div>
        </article>

        <aside className="panel">
          <p className="eyebrow blue">No card storage</p>
          <h3>InQsi should not collect raw card numbers.</h3>
          <p className="movement">The safe structure is simple: InQsi manages the member account and access state. The payment provider manages card entry, card updates, invoices, and subscription billing.</p>
          <p className="movement">When the provider integration is connected, this page should open a secure portal session for the signed-in customer.</p>
          <div className="compliance-box" style={{ marginTop: 14 }}>
            Portal connection placeholder: connect this button to the payment provider hosted billing portal before launch.
          </div>
        </aside>
      </section>
    </main>
  );
}
