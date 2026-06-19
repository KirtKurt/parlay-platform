'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { getPlan } from '@/lib/subscription';

export function CheckoutPanel() {
  const searchParams = useSearchParams();
  const plan = getPlan(searchParams.get('plan'));
  const email = searchParams.get('email');

  return (
    <section className="content-grid">
      <div className="panel" style={{ display: 'grid', gap: 18 }}>
        <div>
          <p className="eyebrow blue">Secure checkout</p>
          <h3>{plan.name} monthly subscription</h3>
        </div>
        <div className="checkout-summary">
          <span>Selected plan</span>
          <strong>{plan.name}</strong>
          <p>{plan.description}</p>
        </div>
        <div className="checkout-summary">
          <span>Monthly price</span>
          <strong>{plan.price}</strong>
          <p>Recurring billing every month until canceled.</p>
        </div>
        {email && (
          <div className="checkout-summary">
            <span>Account email</span>
            <strong>{email}</strong>
            <p>This email will receive receipts and account notices.</p>
          </div>
        )}
        <div className="compliance-box">
          Live card entry should happen only through Stripe Checkout or another PCI-compliant hosted payment page. This frontend does not collect or store card numbers.
        </div>
        <div className="hero-actions">
          <Link className="primary-button large" href={`/checkout/success?plan=${plan.id}`} style={{ textDecoration: 'none' }}>Demo checkout success</Link>
          <Link className="ghost-button large" href="/checkout/cancel" style={{ textDecoration: 'none' }}>Cancel</Link>
        </div>
      </div>

      <aside className="panel rank-panel">
        <p className="eyebrow">What live billing needs</p>
        <h3>Stripe + Cognito handoff</h3>
        <div className="rank-list" style={{ marginTop: 18 }}>
          <div className="rank-card"><h4>1. Create user</h4><p>Register user profile and age/residence confirmation in Cognito/DynamoDB.</p></div>
          <div className="rank-card"><h4>2. Create checkout session</h4><p>Backend creates a Stripe Checkout session for the chosen monthly plan.</p></div>
          <div className="rank-card"><h4>3. Webhook confirms access</h4><p>Stripe webhook marks the member active after successful payment.</p></div>
        </div>
      </aside>
    </section>
  );
}
