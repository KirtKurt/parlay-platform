import type { Metadata } from 'next';
import { getAdminHealth } from '@/lib/inqsi-admin-health';

export const metadata: Metadata = {
  title: 'Operator Health',
  description: 'Internal InQsi readiness dashboard.',
  robots: { index: false, follow: false }
};

export default function OperatorPage() {
  const health = getAdminHealth();
  return (
    <main className="inqsi-shell">
      <header className="inqsi-topbar">
        <a className="inqsi-brand" href="/"><span className="inqsi-logo-mark">Q</span><span><b>InQsi</b><small>Operator Health</small></span></a>
        <nav className="inqsi-nav-actions"><a href="/">Home</a><a href="/launch-checklist">Launch checklist</a></nav>
      </header>
      <section className="inqsi-hero">
        <div className="inqsi-hero-card">
          <p className="inqsi-promo">Internal</p>
          <h1>Operator readiness dashboard.</h1>
          <p>Generated at {health.generatedAt}. This page shows infrastructure readiness only and does not expose user records.</p>
        </div>
        <aside className="inqsi-signup-card">
          <h2>Next actions</h2>
          {health.actionItems.length ? health.actionItems.map((item) => <p key={item}>{item}</p>) : <p>All monitored launch items are ready.</p>}
        </aside>
      </section>
      <section className="inqsi-feature-grid">
        <article><b>Data layer</b><span>{health.dataLayer}</span></article>
        <article><b>Record storage</b><span>{health.recordStorage}</span></article>
        <article><b>Access</b><span>{health.access}</span></article>
        <article><b>Analytics</b><span>{health.monitoring.analyticsEndpointReady ? 'ready' : 'working_on_it'}</span></article>
        <article><b>Error tracking</b><span>{health.monitoring.errorTrackingReady ? 'ready' : 'working_on_it'}</span></article>
        <article><b>Uptime</b><span>{health.monitoring.uptimeReady ? 'ready' : 'working_on_it'}</span></article>
      </section>
    </main>
  );
}
