import Link from 'next/link';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { GameCard } from '@/components/GameCard';
import { RankingCard } from '@/components/RankingCard';
import { LineMovementGraph } from '@/components/LineMovementGraph';
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
            Silvers Syndicate is moving from a single demo board into a full sports market terminal: sport pages,
            game detail pages, T-snapshot timelines, 15-minute line movement, Top-3 containment logic, and refusal when the data is not safe enough.
          </p>
          <div className="hero-actions">
            <Link className="primary-button large" href="/parlays/build" style={{ textDecoration: 'none' }}>Build Parlay</Link>
            <Link className="ghost-button large" href="/sports" style={{ textDecoration: 'none' }}>View Sports Lobby</Link>
            <Link className="ghost-button large" href={`/game/${games[0]?.id ?? 'nfl-001'}`} style={{ textDecoration: 'none' }}>Open Game Detail</Link>
          </div>
        </div>

        <aside className="bet-slip glass-card">
          <div className="slip-head">
            <span>Parlay Slip</span>
            <strong>3 Legs</strong>
          </div>
          {rankings[0].legs.map((leg) => (
            <div className="slip-leg" key={leg}>
              <span>{leg}</span>
              <b>Selected</b>
            </div>
          ))}
          <div className="slip-total">
            <span>Rank #1</span>
            <strong>{rankings[0].american}</strong>
          </div>
          <p className="slip-note">{rankings[0].note}</p>
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
    </main>
  );
}
