import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { RegisterForm } from '@/components/RegisterForm';

export default function RegisterPage() {
  return (
    <main className="shell">
      <AppHeader title="Create account" />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Monthly membership</p>
        <h2>Register once, then unlock the sports market terminal.</h2>
        <p className="hero-copy">
          Collect the customer profile, confirm age and location, choose a recurring monthly plan, then hand payment to a secure hosted checkout flow.
        </p>
        <div className="hero-actions">
          <Link className="ghost-button large" href="/pricing" style={{ textDecoration: 'none' }}>Compare plans</Link>
          <Link className="ghost-button large" href="/login" style={{ textDecoration: 'none' }}>Already a member?</Link>
        </div>
      </section>
      <RegisterForm />
    </main>
  );
}
