import type { Metadata } from 'next';
import { getReleaseReadiness } from '@/lib/inqsi-release-checklist';

export const metadata: Metadata = {
  title: 'Release Checklist',
  description: 'InQsi release readiness checklist.',
  alternates: { canonical: '/release-checklist' }
};

export default function ReleaseChecklistPage() {
  const readiness = getReleaseReadiness();
  return (
    <main className="inqsi-shell">
      <header className="inqsi-topbar">
        <a className="inqsi-brand" href="/"><span className="inqsi-logo-mark">Q</span><span><b>InQsi</b><small>Release checklist</small></span></a>
        <nav className="inqsi-nav-actions"><a href="/operator">Operator</a><a href="/">Home</a></nav>
      </header>
      <section className="inqsi-hero">
        <div className="inqsi-hero-card">
          <p className="inqsi-promo">Release readiness</p>
          <h1>{readiness.ready}/{readiness.total} ready.</h1>
          <p>Checklist status for the next release. Items marked Working on it should not be treated as live-complete.</p>
        </div>
        <aside className="inqsi-signup-card"><h2>Status</h2><p>Needs review: {readiness.needsReview}</p><p>Working on it: {readiness.workingOnIt}</p></aside>
      </section>
      <section className="inqsi-feature-grid">
        {readiness.checklist.map((item) => <article key={item.id}><b>{item.title}</b><span>{item.status} · {item.detail}</span></article>)}
      </section>
    </main>
  );
}
