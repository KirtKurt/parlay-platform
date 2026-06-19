import { AppHeader } from '@/components/AppHeader';

export default function AccountPage() {
  return (
    <main className="shell">
      <AppHeader title="Account" />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Member workspace</p>
        <h2>Account controls will manage subscription, saved sports, and alerts.</h2>
        <p className="hero-copy">This is a placeholder shell for authentication and billing. It gives us the route structure now so the SaaS layer can plug in later without redesigning the app.</p>
      </section>
      <section className="status-row">
        <article className="status-card"><span>Status</span><strong>Demo</strong><p>Authentication not connected yet.</p></article>
        <article className="status-card"><span>Plan</span><strong>Preview</strong><p>Subscription checkout will connect here.</p></article>
        <article className="status-card"><span>Saved Sports</span><strong>8</strong><p>NFL, CFB, NBA, NCAAM, NHL, MLB, Tennis, Soccer.</p></article>
        <article className="status-card"><span>Alerts</span><strong>Ready</strong><p>Steam, resistance, chaos, and market anomaly alerts.</p></article>
      </section>
    </main>
  );
}
