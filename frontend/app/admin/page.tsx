import Link from 'next/link';
import { MASTER_ACCESS, SUBSCRIBER_ACCESS, VISITOR_ACCESS } from '@/lib/accessControl';

const adminChecks = [
  'Verify backend health and API connectivity',
  'Review customer subscription state from billing provider confirmation',
  'Override support access only through server-side master role',
  'Inspect slate ingestion status by sport',
  'Review locked premium outputs before public release'
];

export default function AdminPage() {
  return (
    <main className="shell">
      <section className="hero-card glass-card">
        <p className="eyebrow blue">Master Console</p>
        <h2>Admin access is server-side only.</h2>
        <p className="hero-copy">
          This page is the frontend placeholder for the Silvers Syndicate master login flow. No master password is stored in the frontend. The real unlock must come from the backend after identity, role, and subscription status are verified.
        </p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/login">Master Sign In</Link>
          <Link className="ghost-button large" href="/account">Account Status</Link>
        </div>
      </section>

      <section className="status-row">
        <article className="status-card">
          <span>Visitor</span>
          <strong>{VISITOR_ACCESS.label}</strong>
          <p>Teaser view only. Premium rankings, graph detail, and parlay outputs stay blurred.</p>
        </article>
        <article className="status-card">
          <span>Paid Subscriber</span>
          <strong>{SUBSCRIBER_ACCESS.label}</strong>
          <p>Full board unlock after monthly billing provider confirms active status.</p>
        </article>
        <article className="status-card">
          <span>Master</span>
          <strong>{MASTER_ACCESS.label}</strong>
          <p>Full access plus admin tools. Must be assigned by backend role, not client code.</p>
        </article>
        <article className="status-card">
          <span>Billing</span>
          <strong>Provider-neutral</strong>
          <p>Your payment provider credentials will be added later. No Stripe dependency.</p>
        </article>
      </section>

      <section className="content-grid">
        <div className="panel">
          <div className="panel-header compact">
            <div>
              <p className="eyebrow">Admin Readiness</p>
              <h3>Master console checklist</h3>
            </div>
          </div>
          <div className="game-list">
            {adminChecks.map((item) => (
              <article className="game-card" key={item}>
                <div className="game-topline">
                  <span className="league-chip">CONTROL</span>
                  <span>Required</span>
                  <span className="data-status collected">Planned</span>
                </div>
                <h4>{item}</h4>
                <p className="movement">This must be enforced on the server before any real customer or billing data is exposed.</p>
              </article>
            ))}
          </div>
        </div>

        <aside className="panel rank-panel">
          <div className="panel-header compact">
            <div>
              <p className="eyebrow">Access Rules</p>
              <h3>No client-side secrets</h3>
            </div>
          </div>
          <div className="rank-list">
            <article className="rank-card top-zone">
              <div className="rank-head"><span>Rule #1</span><b>LOCKED</b></div>
              <h4>No master password in frontend code</h4>
              <p>Frontend code is public to the browser. Master access must come from authenticated backend claims.</p>
            </article>
            <article className="rank-card">
              <div className="rank-head"><span>Rule #2</span></div>
              <h4>Billing provider confirms active status</h4>
              <p>The payment provider sends confirmation to the backend, then the backend unlocks the user.</p>
            </article>
            <article className="rank-card">
              <div className="rank-head"><span>Rule #3</span></div>
              <h4>Master role bypasses subscription gate</h4>
              <p>Master users can inspect premium and admin views without monthly billing status.</p>
            </article>
          </div>
        </aside>
      </section>
    </main>
  );
}
