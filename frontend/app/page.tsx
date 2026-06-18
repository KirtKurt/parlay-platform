import { games, rankings, statusCards } from '@/lib/mockData';
import { SignalPill } from '@/components/SignalPill';

export default function Home() {
  return (
    <main className="shell">
      <nav className="topbar">
        <div className="brand-block">
          <div className="brand-mark">RC</div>
          <div>
            <p className="eyebrow">Risk Check Syndicate</p>
            <h1>Sportsbook-style parlay intelligence</h1>
          </div>
        </div>
        <div className="nav-actions">
          <button className="ghost-button">Today</button>
          <button className="primary-button">Build Parlay</button>
        </div>
      </nav>

      <section className="hero-grid">
        <div className="hero-card glass-card">
          <p className="eyebrow blue">Market Intelligence Engine</p>
          <h2>Stop guessing. Check steam, resistance, traps, and coin-flip risk before you build.</h2>
          <p className="hero-copy">
            A mobile-first sportsbook feel with our own signal discipline: T-snapshots, multi-book confirmation,
            natural structure, Top-3 containment, and refusal when the data is not safe enough.
          </p>
          <div className="hero-actions">
            <button className="primary-button large">Run Demo Build</button>
            <button className="ghost-button large">View Slate</button>
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

      <section className="content-grid">
        <div className="panel slate-panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Today’s Board</p>
              <h3>Eligible games</h3>
            </div>
            <div className="league-tabs">
              <span className="active">NFL</span>
              <span>CFB</span>
              <span>NBA</span>
              <span>NHL</span>
            </div>
          </div>

          <div className="game-list">
            {games.map((game) => (
              <article className="game-card" key={game.id}>
                <div className="game-topline">
                  <span className="league-chip">{game.league}</span>
                  <span>{game.start}</span>
                  <span className={`data-status ${game.dataStatus.toLowerCase()}`}>{game.dataStatus}</span>
                </div>
                <h4>{game.matchup}</h4>
                <div className="market-row">
                  <div>
                    <span>Favorite</span>
                    <strong>{game.favorite}</strong>
                    <b>{game.favoriteMl}</b>
                  </div>
                  <div>
                    <span>Underdog</span>
                    <strong>{game.underdog}</strong>
                    <b>{game.underdogMl > 0 ? `+${game.underdogMl}` : game.underdogMl}</b>
                  </div>
                  <div>
                    <span>Total</span>
                    <strong>O/U</strong>
                    <b>{game.total}</b>
                  </div>
                </div>
                <p className="movement">{game.movement}</p>
                <div className="signal-row">
                  {game.signals.map((signal) => <SignalPill signal={signal} key={`${game.id}-${signal}`} />)}
                </div>
              </article>
            ))}
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
            {rankings.map((ranking) => (
              <article className={`rank-card ${ranking.topZone ? 'top-zone' : ''}`} key={ranking.rank}>
                <div className="rank-head">
                  <span>Rank #{ranking.rank}</span>
                  {ranking.topZone && <b>TOP-3</b>}
                </div>
                <h4>{ranking.legs.join(' × ')}</h4>
                <div className="rank-meta">
                  <span>{ranking.american}</span>
                  <span>{ranking.implied}</span>
                  <span>{ranking.structure}</span>
                </div>
                <p>{ranking.note}</p>
                <div className={`risk risk-${ranking.risk.toLowerCase()}`}>{ranking.risk} RISK</div>
              </article>
            ))}
          </div>
        </aside>
      </section>
    </main>
  );
}
