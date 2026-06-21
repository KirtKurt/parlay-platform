import Link from 'next/link';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { GameCard } from '@/components/GameCard';
import { RankingCard } from '@/components/RankingCard';
import { PaidPreviewGate } from '@/components/PaidPreviewGate';

export default async function BuildParlayPage() {
  const { games, rankings } = await getApiSnapshot();
  const anchorCandidates = games.filter((game) => game.risk !== 'HIGH').slice(0, 3);
  const cautionQueue = games.filter((game) => game.risk === 'HIGH');

  return (
    <main className="shell">
      <AppHeader title="AI Slip Builder" />

      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Build with discipline</p>
        <h2>Choose up to 3 legs. Check the structure. Do not force the slip.</h2>
        <p className="hero-copy">The AI Slip Builder helps turn selected games into a cleaner 3-leg slip. InQsi does not build parlays with more than 3 legs. It looks for strong anchors, flags the coin-flip leg, checks zero-overlap options, and warns you when the market is not supporting the build.</p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/register" style={{ textDecoration: 'none' }}>Start 5 Days Free</Link>
          <Link className="ghost-button large" href="/pricing" style={{ textDecoration: 'none' }}>View Pricing</Link>
          <Link className="ghost-button large" href="/parlay-scanner" style={{ textDecoration: 'none' }}>Scan Existing Slip</Link>
        </div>
      </section>

      <section className="status-row">
        <article className="status-card"><span>Slip type</span><strong>3-leg max</strong><p>No 4-leg, 5-leg, or larger parlay builds.</p></article>
        <article className="status-card"><span>Anchor check</span><strong>2 Solid</strong><p>Looks for at least two legs with cleaner support.</p></article>
        <article className="status-card"><span>Variable leg</span><strong>0-1 CF</strong><p>Coin-flip exposure stays visible instead of hidden.</p></article>
        <article className="status-card"><span>Overlap</span><strong>Zero</strong><p>Multi-slip builds avoid repeating the same team when possible.</p></article>
      </section>

      <PaidPreviewGate title="Unlock the full AI Slip Builder" message="Preview the discipline now. Full 3-leg ranked builds, weak-leg review, and saved slip history unlock with member access.">
        <section className="content-grid">
          <div className="panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Anchor candidates</p>
                <h3>Start with the legs that look cleaner.</h3>
              </div>
            </div>
            <p className="movement">Anchor candidates are the games InQsi sees as having cleaner market support. They are not guarantees. They are the legs that deserve to be checked first when building a 3-leg slip.</p>
            <div className="game-list">
              {anchorCandidates.map((game) => <GameCard game={game} key={game.id} />)}
            </div>
          </div>

          <aside className="panel rank-panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Ranked structure</p>
                <h3>Current top paths</h3>
              </div>
            </div>
            <p className="movement">The builder ranks the strongest-looking 3-leg structure first, then shows where the risk starts to enter the slip.</p>
            <div className="rank-list">
              {rankings.slice(0, 3).map((ranking) => <RankingCard ranking={ranking} key={ranking.rank} />)}
            </div>
            <Link className="ghost-button" href="/parlays" style={{ display: 'inline-block', marginTop: 16, textDecoration: 'none' }}>View builder overview</Link>
          </aside>
        </section>

        {cautionQueue.length > 0 && (
          <section className="panel" style={{ marginTop: 20 }}>
            <div className="panel-header">
              <div>
                <p className="eyebrow">Caution queue</p>
                <h3>Games that need a slower review</h3>
              </div>
            </div>
            <p className="movement">These games may still be useful, but InQsi is flagging them for extra caution because the risk profile is less clean.</p>
            <div className="game-list">
              {cautionQueue.map((game) => <GameCard game={game} key={game.id} />)}
            </div>
          </section>
        )}
      </PaidPreviewGate>
    </main>
  );
}
