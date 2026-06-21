import type { Metadata } from 'next';
import Link from 'next/link';
import { getProviderOverallStatus } from '@/lib/inqsi-data-provider';
import { getAuthReadiness, getSubscriptionReadiness } from '@/lib/inqsi-auth-billing';
import { getMonitoringReadiness } from '@/lib/inqsi-observability';
import { getLaunchReadiness } from '@/lib/inqsi-launch-checklist';

export const metadata: Metadata = {
  title: 'Admin Dashboard',
  description: 'Private InQsi operator dashboard readiness shell.',
  robots: { index: false, follow: false },
  alternates: { canonical: '/admin' }
};

function StatusCard({ title, value, copy }: { title: string; value: string; copy: string }) {
  return <article><b>{title}</b><span>{value}</span><small>{copy}</small></article>;
}

export default function AdminPage() {
  const providers = getProviderOverallStatus();
  const auth = getAuthReadiness();
  const subscription = getSubscriptionReadiness();
  const monitoring = getMonitoringReadiness();
  const launch = getLaunchReadiness();

  return (
    <main className="inqsi-shell">
      <header className="inqsi-topbar">
        <Link className="inqsi-brand" href="/"><span className="inqsi-logo-mark">Q</span><span><b>InQsi</b><small>Operator Dashboard</small></span></Link>
        <nav className="inqsi-nav-actions"><Link href="/launch-checklist">Launch checklist</Link><Link href="/privacy-choices">Privacy</Link></nav>
      </header>

      <section className="inqsi-hero inqsi-seo-hero">
        <div className="inqsi-hero-card">
          <p className="inqsi-promo">Private admin shell</p>
          <h1>Operator dashboard readiness.</h1>
          <p>This page is the internal command center shell for providers, auth, billing, analytics, launch readiness, and data status. Private admin auth still needs to be connected before exposing real operational data.</p>
        </div>
        <aside className="inqsi-signup-card">
          <h2>Launch readiness</h2>
          <p>{launch.percentReady}% ready · {launch.blocked} blocked items</p>
          <a href="/api/admin/summary">View JSON summary</a>
          <small>Robots are set to noindex for this page.</small>
        </aside>
      </section>

      <section className="inqsi-feature-grid">
        <StatusCard title="Providers" value={providers.ready ? 'Ready' : 'Working on it'} copy={providers.message} />
        <StatusCard title="Auth" value={auth.ready ? 'Ready' : 'Working on it'} copy={auth.message} />
        <StatusCard title="Subscription" value={subscription.ready ? 'Ready' : 'Working on it'} copy={subscription.message} />
        <StatusCard title="Monitoring" value={monitoring.analyticsEndpointReady ? 'Partially ready' : 'Working on it'} copy={monitoring.message} />
      </section>

      <section className="inqsi-panel">
        <div className="inqsi-section-head"><h2>Launch items</h2><span>{launch.blocked} blocked</span></div>
        <div className="inqsi-game-list">
          {launch.items.map((item) => (
            <article className="inqsi-mini-card" key={item.id}>
              <b>{item.area}: {item.title}</b>
              <small>{item.status.toUpperCase()} · {item.note}</small>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}
