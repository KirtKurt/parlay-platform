import type { Metadata } from 'next';
import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { LoginForm } from '@/components/LoginForm';

export const metadata: Metadata = {
  title: 'Login | InQsi',
  description: 'Log in to InQsi to return to your saved slips, watchlists, alerts, and market review tools.',
  alternates: { canonical: '/login' }
};

export default function LoginPage() {
  return (
    <main className="inqsi-shell">
      <AppHeader eyebrow="InQsi" title="Member Login" />
      <section className="inqsi-hero inqsi-seo-hero">
        <div className="inqsi-hero-card">
          <p className="inqsi-promo">Member access</p>
          <h1>Log in to your InQsi workspace.</h1>
          <p>Use your email to open the member workspace, review active sport boards, scan slips, and save your watchlist.</p>
          <div className="inqsi-stat-grid">
            <div><b>Active board</b><span>Market data, odds, spread, and O/U.</span></div>
            <div><b>Slip scanner</b><span>Find weak legs before lock-in.</span></div>
            <div><b>Watchlist</b><span>Track games you care about.</span></div>
            <div><b>Review history</b><span>Learn from results.</span></div>
          </div>
        </div>
        <aside className="inqsi-signup-card">
          <LoginForm />
          <small style={{ display: 'block', marginTop: 12 }}>No account yet? <Link href="/register">Create one here.</Link></small>
        </aside>
      </section>
    </main>
  );
}
