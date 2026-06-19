import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';

export default function CheckoutCancelPage() {
  return (
    <main className="shell">
      <AppHeader title="Checkout" />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Return to plans</p>
        <h2>Choose a plan when you are ready.</h2>
        <p className="hero-copy">The membership flow can restart from pricing or registration.</p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/pricing" style={{ textDecoration: 'none' }}>Review pricing</Link>
          <Link className="ghost-button large" href="/register" style={{ textDecoration: 'none' }}>Registration</Link>
        </div>
      </section>
    </main>
  );
}
