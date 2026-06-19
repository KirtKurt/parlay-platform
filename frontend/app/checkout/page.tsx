import { Suspense } from 'react';
import { AppHeader } from '@/components/AppHeader';
import { CheckoutPanel } from '@/components/CheckoutPanel';

export default function CheckoutPage() {
  return (
    <main className="shell">
      <AppHeader title="Checkout" />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Recurring billing</p>
        <h2>Confirm the monthly plan before secure payment.</h2>
        <p className="hero-copy">This page is the bridge between registration and the hosted payment provider. Live payment details should be handled by a PCI-compliant checkout page.</p>
      </section>
      <Suspense fallback={<div className="panel">Loading checkout…</div>}>
        <CheckoutPanel />
      </Suspense>
    </main>
  );
}
