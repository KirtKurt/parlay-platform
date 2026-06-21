import type { Metadata } from 'next';
import { OperatorNav } from '@/components/OperatorNav';
import { getAdminHealth } from '@/lib/inqsi-admin-health';
import { OPERATOR_DASHBOARD_CARDS, formatStatus } from '@/lib/inqsi-operator-dashboard';

export const metadata: Metadata = {
  title: 'Operator Command Center',
  description: 'Internal InQsi operator dashboard.',
  robots: { index: false, follow: false }
};

export default function OperatorPage() {
  const health = getAdminHealth();
  return (
    <main className="inqsi-shell">
      <header className="inqsi-topbar">
        <a className="inqsi-brand" href="/"><span className="inqsi-logo-mark">Q</span><span><b>InQsi</b><small>Operator Command Center</small></span></a>
        <OperatorNav />
      </header>
      <section className="inqsi-hero">
        <div className="inqsi-hero-card">
          <p className="inqsi-promo">Internal dashboard</p>
          <h1>Operator command center.</h1>
          <p>Generated at {health.generatedAt}. This dashboard is built to manage members, creators, attribution, data health, privacy, support, and launch readiness.</p>
        </div>
        <aside className="inqsi-signup-card">
          <h2>Immediate actions</h2>
          {health.actionItems.length ? health.actionItems.slice(0, 5).map((item) => <p key={item}>{item}</p>) : <p>All monitored launch items are ready.</p>}
        </aside>
      </section>
      <section className="inqsi-feature-grid">
        {OPERATOR_DASHBOARD_CARDS.map((card) => (
          <article key={card.title}>
            <b>{card.title}</b>
            <span>{card.value} · {formatStatus(card.status)}</span>
            <p>{card.detail}</p>
            {card.href && <a href={card.href}>Open</a>}
          </article>
        ))}
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
