import type { Metadata } from 'next';
import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { RegisterForm } from '@/components/RegisterForm';

export const metadata: Metadata = {
  title: 'Create Account | InQsi',
  description: 'Create an InQsi account and start the 5-day free promo.',
  alternates: { canonical: '/register' }
};

export default function RegisterPage() {
  return (
    <main className="inqsi-shell">
      <AppHeader eyebrow="InQsi" title="Create Account" />
      <section className="inqsi-hero inqsi-seo-hero">
        <div className="inqsi-hero-card">
          <p className="inqsi-promo">5 days free</p>
          <h1>Start your InQsi workspace.</h1>
          <p>Create your account, open the member workspace, scan slips, review sport boards, and see whether InQsi earns a place in your routine.</p>
          <div className="inqsi-stat-grid">
            <div><b>Quick access</b><span>Create your workspace without a complicated setup.</span></div>
            <div><b>AI Slip Scanner</b><span>Bring the picks you already like.</span></div>
            <div><b>Sports board</b><span>Odds, spread, O/U, and market movement.</span></div>
            <div><b>Full access</b><span>Member tools after registration.</span></div>
          </div>
        </div>
        <aside className="inqsi-signup-card">
          <RegisterForm />
          <small style={{ display: 'block', marginTop: 12 }}>Already have access? <Link href="/login">Log in here.</Link></small>
        </aside>
      </section>
    </main>
  );
}
