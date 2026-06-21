import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { TeamJerseyBadge } from '@/components/SportVisuals';

export const metadata = {
  title: 'My Slips & Scores',
  description: 'Control slip visibility, review post-game analysis, and track InQsi accuracy scores over time.',
  alternates: { canonical: '/account/slips' }
};

const scoreWindows = ['Individual parlay', '1 day', '1 week', '1 month', '3 months', '1 year'];

export default function MySlipsPage() {
  return (
    <main className="shell">
      <AppHeader title="My Slips & Scores" />

      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Saved slips</p>
        <div className="team-badge-row" style={{ marginTop: 10 }}>
          <TeamJerseyBadge abbr="PUB" tone="gold" number="1" />
          <TeamJerseyBadge abbr="PRI" tone="blue" number="0" />
          <TeamJerseyBadge abbr="ACC" tone="teal" number="%" />
        </div>
        <h2>Choose what stays private and what can be shown publicly.</h2>
        <p className="hero-copy">Customers should be able to save slips privately by default, then choose which slips can be displayed publicly. Other customers cannot comment on slips for now.</p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/parlays" style={{ textDecoration: 'none' }}>Build My Slip</Link>
          <Link className="ghost-button large" href="/parlay-scanner" style={{ textDecoration: 'none' }}>Scan My Slip</Link>
          <Link className="ghost-button large" href="/account" style={{ textDecoration: 'none' }}>Back to Account</Link>
        </div>
      </section>

      <section className="status-row">
        <article className="status-card"><TeamJerseyBadge abbr="PRI" tone="blue" number="0" /><span>Default</span><strong>Private</strong><p>New slips should stay private unless the customer makes them public.</p></article>
        <article className="status-card"><TeamJerseyBadge abbr="PUB" tone="gold" number="1" /><span>Public Display</span><strong>Optional</strong><p>Customers choose which slips can appear publicly.</p></article>
        <article className="status-card"><TeamJerseyBadge abbr="COM" tone="teal" number="0" /><span>Comments</span><strong>Off</strong><p>Other customers cannot comment on slips for now.</p></article>
        <article className="status-card"><TeamJerseyBadge abbr="ACC" tone="mint" number="%" /><span>Accuracy</span><strong>Post-game</strong><p>Scores update after games are final.</p></article>
      </section>

      <section className="content-grid" style={{ marginTop: 20 }}>
        <article className="panel">
          <div className="panel-header compact">
            <div>
              <p className="eyebrow blue">Accuracy tracking</p>
              <h3>Score each slip after the games are over.</h3>
            </div>
          </div>
          <p className="movement">When the games finish, InQsi should run a post-game analysis and score the saved slip. The customer should see accuracy for the individual parlay and combined accuracy across multiple time windows.</p>
          <div className="feature-grid" style={{ marginTop: 16 }}>
            {scoreWindows.map((window) => (
              <article key={window}>
                <b>{window}</b>
                <span>Accuracy score</span>
              </article>
            ))}
          </div>
        </article>

        <aside className="panel">
          <p className="eyebrow blue">Future challenges</p>
          <h3>Competition can come later.</h3>
          <p className="movement">The scoring foundation should be built now so competitive challenges can be added later without rebuilding the account history model.</p>
          <p className="movement">Future challenge mode could rank public slips by accuracy, sport, date range, or challenge event. For now, the platform should focus on saved slips, private/public display, and customer score history.</p>
        </aside>
      </section>

      <section className="panel" style={{ marginTop: 20 }}>
        <div className="panel-header compact">
          <div>
            <p className="eyebrow blue">Data rules</p>
            <h3>What needs to be stored later</h3>
          </div>
        </div>
        <div className="game-list">
          <article className="game-card"><div className="game-topline"><span className="league-chip">Visibility</span><span>Customer choice</span></div><h4>Private or public</h4><p className="movement">Each slip needs a visibility setting controlled by the owner.</p></article>
          <article className="game-card"><div className="game-topline"><span className="league-chip">Finals</span><span>Post-game</span></div><h4>Score after results are final</h4><p className="movement">InQsi should wait for completed game results before calculating the final accuracy score.</p></article>
          <article className="game-card"><div className="game-topline"><span className="league-chip">History</span><span>Rolling windows</span></div><h4>Track combined score over time</h4><p className="movement">Store scores so customers can see 1 day, 1 week, 1 month, 3 months, and 1 year views.</p></article>
        </div>
      </section>
    </main>
  );
}
