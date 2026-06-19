import Link from 'next/link';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { GameCard } from '@/components/GameCard';
import { RankingCard } from '@/components/RankingCard';
import { LineMovementGraph } from '@/components/LineMovementGraph';
import { PaidPreviewGate } from '@/components/PaidPreviewGate';
import { sports } from '@/lib/sports';

export default async function Home() {
  const { games, rankings, statusCards, lineMovement, apiStatus, apiDetail } = await getApiSnapshot();

  return (
    <main className="shell">
      <AppHeader apiStatus={apiStatus} apiDetail={apiDetail} />

      <section className="hero-grid">
        <div className="hero-card glass-card">
          <p className="eyebrow blue">Market Intelligence Terminal</p>
          <h2>One lobby for every sport, every slate, every game, every parlay risk decision.</h2>
          <p className="hero-copy">
            Silvers Syndicate gives visitors a preview of the market board, then unlocks the full terminal after registration: sport pages,
            game detail pages, T-snapshot timelines, 15-minute line movement, Top-3 containment logic, and refusal when the data is not safe enough.
          </p>
          <div className="hero-actions">
            <Link className="primary-button large" href="/register" style={{ textDecoration: 'none' }}>Join Monthly</Link>
            <Link className="ghost-button large" href="/sports" style={{ textDecoration: 'none' }}>Preview Sports Lobby</Link>
            <Link className="ghost-button large" href="/login" style={{ textDecoration: 'none' }}>Log In</Link>
          </div>
        </div>

        <aside className="bet-slip glass-card">
          <div className="slip-head">
            <span>Preview Slip</span>
            <strong>Locked</strong>
          </div>
          {rankings[0].legs.slice(0, 2).map((leg) => (
            <div className="slip-leg" key={leg}>
              <span>{leg}</span>
              <b>Preview</b>
            </div>
          ))}
          <div className="slip-leg">
            <span>Premium leg hidden</span>
            <b>Members only</b>
          </div>
          <div className="slip-total">
            <span>Rank #1</span>
            <strong>LOCKED</strong>
          </div>
          <p className="slip-note">Register to unlock full Top-8 rankings, true coin-flip markers, and reason-coded refusals.</p>
        </aside>
      </section>

      <section className="status-row">
        {statusCards.map((card) => (
          <article className="status-card" key={card.label}>
            <span>{card.label}</span>
            <strong>{card.value}</strong>
            <p>{card.detail}</p>
          </article>
        ))}
      </section>

      <section className="panel" style={{ marginBottom: 20 }}>
        <div className="panel-header">
          <div>
            <p className="eyebrow">Sport Pages</p>
            <h3>Expandable market boards</h3>
          </div>
          <Link className="ghost-button" href="/sports" style={{ textDecoration: 'none' }}>All Sports</Link>
        </div>
        <div className="league-tabs">
          {sports.map((sport) => (
            <Link className="ghost-button" href={`/sports/${sport.slug}`} key={sport.slug} style={{ textDecoration: 'none' }}>{sport.label}</Link>
          ))}
        </div>
      </section>

      <PaidPreviewGate title="Unlock the full market board">
        <section className="content-grid">
          <div className="panel slate-panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Today’s Board</p>
                <h3>Eligible games</h3>
              </div>
              <div className="league-tabs">
                {sports.slice(0, 4).map((sport, index) => (
                  <Link className={index === 0 ? 'active' : ''} href={`/sports/${sport.slug}`} key={sport.slug} style={{ textDecoration: 'none' }}>{sport.label}</Link>
                ))}
              </div>
            </div>

            <div className="game-list">
              {games.map((game) => <GameCard game={game} key={game.id} />)}
            </div>
          </div>

          <aside className="panel rank-panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">8-Combo Ranking</p>
                <h3>Containment zone</h3>
              </div>
            </div>
            <div className="rank-list">
              {rankings.map((ranking) => <RankingCard ranking={ranking} key={ranking.rank} />)}
            </div>
          </aside>
        </section>

        <LineMovementGraph data={lineMovement} />
      </PaidPreviewGate>
    </main>
  );
}
