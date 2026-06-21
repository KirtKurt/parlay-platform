import type { Metadata } from 'next';
import Link from 'next/link';
import { headers } from 'next/headers';
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

function canOpenDashboard() {
  const expected = process.env.INQSI_DASHBOARD_KEY;
  const supplied = headers().get('x-inqsi-dashboard-key');
  return Boolean(expected && supplied && supplied === expected);
}

function LockedDashboard() {
  return (
    <main className="inqsi-shell">
      <header className="inqsi-topbar">
        <Link className="inqsi-brand" href="/"><span className="inqsi-logo-mark">Q</span><span><b>InQsi</b><small>Restricted Dashboard</small></span></Link>
        <nav className="inqsi-nav-actions"><Link href="/">Home</Link><Link href="/sports">Sports</Link><Link href="/pricing">Pricing</Link></nav>
      </header>
      <section className="inqsi-hero inqsi-seo-hero">
        <div className="inqsi-hero-card">
          <p className="inqsi-promo">Restricted</p>
          <h1>Dashboard locked.</h1>
          <p>This internal dashboard is not available to customers.</p>
        </div>
      </section>
    </main>
  );
}

function StatusCard({ title, value, copy }: { title: string; value: string; copy: string }) {
  return <article><b>{title}</b><span>{value}</span><small>{copy}</small></article>;
}

export default function AdminPage() {
  if (!canOpenDashboard()) return <LockedDashboard />;

  const providers = getProviderOverallStatus();
  const auth = getAuthReadiness();
  const subscription = getSubscriptionReadiness();
  const monitoring = getMonitoringReadiness();
  const launch = getLaunchReadiness();

  return (
    <main className="inqsi-shell">
      <header className="inqsi-topbar">
        <Link className="inqsi-brand" href="/"><span className="inqsi-logo-mark">Q</span><span><b>InQsi</b><small>Operator Dashboard</small></span></Link>
        <nav className="inqsi-nav-actions"><Link href="/operator">Operator</Link><Link href="/operator/traffic">Traffic</Link><Link href="/operator/creators">Creators</Link></nav>
      </header>
      <section className="inqsi-hero inqsi-seo-hero">
        <div className="inqsi-hero-card"><p className="inqsi-promo">Private admin</p><h1>Operator dashboard readiness.</h1><p>Internal command center for providers, account access, analytics, launch readiness, creator attribution, and traffic.</p></div>
        <aside className="inqsi-signup-card"><h2>Launch readiness</h2><p>{launch.percentReady}% ready · {launch.blocked} blocked items</p><small>Robots are set to noindex for this page.</small></aside>
      </section>
      <section className="inqsi-feature-grid">
        <StatusCard title="Providers" value={providers.ready ? 'Ready' : 'Working on it'} copy={providers.message} />
        <StatusCard title="Auth" value={auth.ready ? 'Ready' : 'Working on it'} copy={auth.message} />
        <StatusCard title="Membership" value={subscription.ready ? 'Ready' : 'Working on it'} copy={subscription.message} />
        <StatusCard title="Monitoring" value={monitoring.analyticsEndpointReady ? 'Partially ready' : 'Working on it'} copy={monitoring.message} />
      </section>
      <section className="inqsi-panel"><div className="inqsi-section-head"><h2>Launch items</h2><span>{launch.blocked} blocked</span></div><div className="inqsi-game-list">{launch.items.map((item) => (<article className="inqsi-mini-card" key={item.id}><b>{item.area}: {item.title}</b><small>{item.status.toUpperCase()} · {item.note}</small></article>))}</div></section>
    </main>
  );
}
