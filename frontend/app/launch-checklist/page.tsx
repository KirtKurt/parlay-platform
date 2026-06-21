import type { Metadata } from 'next';
import Link from 'next/link';
import { getLaunchReadiness } from '@/lib/inqsi-launch-checklist';

export const metadata: Metadata = {
  title: 'Launch Checklist',
  description: 'InQsi launch readiness checklist for frontend, data, scoring, accounts, analytics, compliance, and domain setup.',
  alternates: { canonical: '/launch-checklist' }
};

export default function LaunchChecklistPage() {
  const launch = getLaunchReadiness();
  return (
    <main className="inqsi-shell">
      <header className="inqsi-topbar">
        <Link className="inqsi-brand" href="/"><span className="inqsi-logo-mark">Q</span><span><b>InQsi</b><small>Launch Readiness</small></span></Link>
        <nav className="inqsi-nav-actions"><Link href="/admin">Admin</Link><Link href="/api/admin/summary">JSON</Link></nav>
      </header>

      <section className="inqsi-hero inqsi-seo-hero">
        <div className="inqsi-hero-card">
          <p className="inqsi-promo">QA and launch</p>
          <h1>Launch checklist.</h1>
          <p>This page tracks the launch-critical items across frontend, data, scoring, accounts, billing, analytics, privacy, and domain setup. Blocked items are not hidden.</p>
        </div>
        <aside className="inqsi-signup-card">
          <h2>{launch.percentReady}% ready</h2>
          <p>{launch.ready} ready · {launch.blocked} blocked · {launch.total} total</p>
          <a href="/api/readiness">Readiness API</a>
          <small>No silent fallbacks. Blocked means a real key, provider, storage layer, or review is still required.</small>
        </aside>
      </section>

      <section className="inqsi-panel">
        <div className="inqsi-section-head"><h2>Checklist</h2><span>{launch.total} items</span></div>
        <div className="inqsi-game-list">
          {launch.items.map((item) => (
            <article className="inqsi-game-card" key={item.id}>
              <div className="inqsi-game-row"><b>{item.title}</b><span className="inqsi-score-chip">{item.status}</span></div>
              <p>{item.area} · {item.owner}</p>
              <div className="inqsi-signal-row"><span>{item.note}</span></div>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}
