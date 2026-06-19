import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';

export default function AccountPage() {
  return (
    <main className="shell">
      <AppHeader title="Account" />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Member workspace</p>
        <h2>Manage subscription status, profile details, saved sports, and alerts.</h2>
        <p className="hero-copy">This page is ready for the live member record. Production should connect the status cards to Cognito, DynamoDB, and the billing provider.</p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/register" style={{ textDecoration: 'none' }}>Create account</Link>
          <Link className="ghost-button large" href="/login" style={{ textDecoration: 'none' }}>Login</Link>
        </div>
      </section>
      <section className="status-row">
        <article className="status-card"><span>Status</span><strong>Demo member</strong><p>Live status should come from authentication and subscription records.</p></article>
        <article className="status-card"><span>Plan</span><strong>Core</strong><p>Primary $35/month access tier.</p></article>
        <article className="status-card"><span>Saved Sports</span><strong>11</strong><p>NFL, CFB, NBA, NCAAM, NHL, MLB, Tennis, Soccer, Darts, Lacrosse, Table Tennis.</p></article>
        <article className="status-card"><span>Alerts</span><strong>Ready</strong><p>Steam, resistance, chaos, and market anomaly alerts.</p></article>
      </section>
    </main>
  );
}
