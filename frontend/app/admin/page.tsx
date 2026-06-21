import type { Metadata } from 'next';
import { notFound, redirect } from 'next/navigation';
import { AppHeader } from '@/components/AppHeader';
import { adminAuditEvents, adminFeatureFlags, adminMembers, adminSeoPages, adminSlipReviews, adminSupportItems, adminTrafficSources } from '@/lib/inqsi-admin-data';
import { hasInternalSession, isInternalPortalEnabled } from '@/lib/internal-access';

export const metadata: Metadata = {
  title: 'Internal Admin | InQsi',
  robots: { index: false, follow: false }
};

export default function Page() {
  if (!isInternalPortalEnabled()) notFound();
  if (!hasInternalSession()) redirect('/admin/login');

  const publicCards = adminMembers.filter((member) => member.publicCard).length;
  const totalVisits = adminTrafficSources.reduce((sum, source) => sum + source.visits, 0);

  return (
    <main className="shell">
      <AppHeader eyebrow="InQsi" title="Internal Admin" />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Owner portal</p>
        <h2>Internal admin dashboard</h2>
        <p className="hero-copy">Owner-only view for members, score-card visibility, source attribution, SEO operations, IndexNow, support notes, audit events, feature flags, and content settings.</p>
      </section>

      <section className="status-row" style={{ marginBottom: 20 }}>
        <article className="status-card"><span>Members</span><strong>{adminMembers.length}</strong><p>Member records in admin view.</p></article>
        <article className="status-card"><span>Public cards</span><strong>{publicCards}</strong><p>Members showing public score cards.</p></article>
        <article className="status-card"><span>Visits</span><strong>{totalVisits}</strong><p>Tracked source and creator traffic.</p></article>
        <article className="status-card"><span>SEO pages</span><strong>{adminSeoPages.length}</strong><p>Pages tracked for search operations.</p></article>
      </section>

      <section className="content-grid">
        <section className="panel"><p className="eyebrow blue">Member management</p><h3>Members and attribution</h3><div className="game-list">{adminMembers.map((member) => <article className="game-card" key={member.id}><div className="game-topline"><span className="league-chip">{member.status}</span><span>{member.source} · {member.creator}</span></div><h4>{member.name}</h4><p className="movement">{member.email} · slips {member.savedSlips} · 1 week {member.weekScore}% · 1 month {member.monthScore}%</p></article>)}</div></section>
        <section className="panel"><p className="eyebrow blue">Slips and scores</p><h3>Visibility review</h3><div className="game-list">{adminSlipReviews.map((slip) => <article className="game-card" key={slip.id}><div className="game-topline"><span className="league-chip">{slip.visibility}</span><span>{slip.result}</span></div><h4>{slip.title}</h4><p className="movement">{slip.member} · {slip.legs} legs · {slip.flag}</p></article>)}</div></section>
      </section>

      <section className="content-grid" style={{ marginTop: 20 }}>
        <section className="panel"><p className="eyebrow blue">Traffic and creators</p><h3>Source attribution</h3><div className="game-list">{adminTrafficSources.map((source) => <article className="game-card" key={source.source}><div className="game-topline"><span className="league-chip">{source.source}</span><span>{source.creator}</span></div><h4>{source.visits} visits</h4><p className="movement">Trials {source.trials} · Paid {source.paid}</p></article>)}</div></section>
        <section className="panel"><p className="eyebrow blue">Support tools</p><h3>Member support notes</h3><div className="game-list">{adminSupportItems.map((item) => <article className="game-card" key={item.id}><div className="game-topline"><span className="league-chip">{item.type}</span><span>{item.status}</span></div><h4>{item.member}</h4><p className="movement">{item.note}</p></article>)}</div></section>
      </section>

      <section className="content-grid" style={{ marginTop: 20 }}>
        <section className="panel"><p className="eyebrow blue">SEO controls</p><h3>Search pages</h3><div className="game-list">{adminSeoPages.map((page) => <article className="game-card" key={page.path}><div className="game-topline"><span className="league-chip">{page.status}</span><span>{page.indexable ? 'indexable' : 'noindex'}</span></div><h4>{page.path}</h4><p className="movement">Last updated {page.lastUpdated}</p></article>)}</div></section>
        <section className="panel"><p className="eyebrow blue">IndexNow</p><h3>Submit public URLs</h3><p className="movement">Trigger URL submission after public SEO or member-card updates. Requires INDEXNOW_KEY in the deployed environment.</p><form action="/api/indexnow" method="post" style={{ marginTop: 14 }}><button className="primary-button" type="submit">Trigger IndexNow</button></form></section>
      </section>

      <section className="content-grid" style={{ marginTop: 20 }}>
        <section className="panel"><p className="eyebrow blue">Audit logs</p><h3>Recent activity</h3><div className="game-list">{adminAuditEvents.map((event) => <article className="game-card" key={event.id}><div className="game-topline"><span className="league-chip">{event.actor}</span><span>{event.createdAt}</span></div><h4>{event.action}</h4><p className="movement">Target: {event.target}</p></article>)}</div></section>
        <section className="panel"><p className="eyebrow blue">Feature flags</p><h3>Launch controls</h3><div className="game-list">{adminFeatureFlags.map((flag) => <article className="game-card" key={flag.key}><div className="game-topline"><span className="league-chip">{flag.enabled ? 'on' : 'off'}</span><span>{flag.key}</span></div><h4>{flag.label}</h4><p className="movement">{flag.note}</p></article>)}</div></section>
      </section>

      <section className="content-grid" style={{ marginTop: 20 }}>
        <section className="panel"><p className="eyebrow blue">Content settings</p><h3>Admin-only notes</h3><div className="game-list"><article className="game-card"><h4>Member language</h4><p className="movement">Use member-first language across public pages.</p></article><article className="game-card"><h4>Quiet interactions</h4><p className="movement">Comments and direct messages remain off at launch.</p></article></div></section>
      </section>
    </main>
  );
}
