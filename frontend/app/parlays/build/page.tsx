import Link from 'next/link';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { GameCard } from '@/components/GameCard';
import { RankingCard } from '@/components/RankingCard';
import { PaidPreviewGate } from '@/components/PaidPreviewGate';

export default async function BuildParlayPage() {
  const { games, rankings, apiStatus, apiDetail } = await getApiSnapshot();
  const anchors = games.filter((game) => game.risk !== 'HIGH').slice(0, 3);
  const reviewQueue = games.filter((game) => game.risk === 'HIGH');

  return (
    <main className="shell">
      <AppHeader title="Build parlay" apiStatus={apiStatus} apiDetail={apiDetail} />

      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Paid builder preview</p>
        <h2>Choose games. Enforce anchors. Refuse unsafe structures.</h2>
        <p className="hero-copy">The builder is the paid workflow: sport selection, number of legs, zero-overlap mode, risk tolerance, eligible games, Top-8 combinations, and reason-coded refusals.</p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/register" style={{ textDecoration: 'none' }}>Unlock Builder</Link>
          <Link className="ghost-button large" href="/pricing" style={{ textDecoration: 'none' }}>View Plans</Link>
          <Link className="ghost-button large" href="/login" style={{ textDecoration: 'none' }}>Log In</Link>
        </div>
      </section>

      <section className="status-row">
        <article className="status-card"><span>Structure</span><strong>3-leg</strong><p>Top-8 two-outcome combinations</p></article>
        <article className="status-card"><span>Gate</span><strong>2 Solid</strong><p>At least two strong solid anchors required</p></article>
        <article className="status-card"><span>Variable</span><strong>0–1 CF</strong><p>No forced coin-flip exposure</p></article>
        <article className="status-card"><span>Overlap</span><strong>Zero</strong><p>Multi-build mode avoids team reuse</p></article>
      </section>

      <PaidPreviewGate title="Builder unlocks after registration" message="Preview the discipline, but hide the actual ranked output until the user has an account and active monthly access.">
        <section className="content-grid">
          <div className="panel">
            <div className="panel-header"><div><p className="eyebrow">Eligible pool</p><h3>Anchor candidates</h3></div></div>
            <div className="game-list">
              {anchors.map((game) => <GameCard game={game} key={game.id} />)}
            </div>
          </div>

          <aside className="panel rank-panel">
            <div className="panel-header compact"><div><p className="eyebrow">Output</p><h3>Current ranking</h3></div></div>
            <div className="rank-list">
              {rankings.slice(0, 3).map((ranking) => <RankingCard ranking={ranking} key={ranking.rank} />)}
            </div>
            <Link className="ghost-button" href="/parlays" style={{ display: 'inline-block', marginTop: 16, textDecoration: 'none' }}>View full ranking</Link>
          </aside>
        </section>

        {reviewQueue.length > 0 && (
          <section className="panel" style={{ marginTop: 20 }}>
            <div className="panel-header"><div><p className="eyebrow">Human Gate</p><h3>Review queue</h3></div></div>
            <div className="game-list">
              {reviewQueue.map((game) => <GameCard game={game} key={game.id} />)}
            </div>
          </section>
        )}
      </PaidPreviewGate>
    </main>
  );
}
