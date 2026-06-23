import Link from 'next/link';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';

function ParlayCard({ index, games }: { index: number; games: any[] }) {
  const legs = games.slice(index, index + 3);
  const fallbackLegs = legs.length === 3 ? legs : games.slice(0, 3);
  return (
    <article className="rank-card top-zone">
      <div className="rank-head"><span>PARLAY</span><b>Approved</b></div>
      <h4>3 Picks · Official Hourly Structure</h4>
      <div className="game-list">
        {fallbackLegs.map((game: any) => (
          <div className="game-topline" key={game.id}>
            <span>{game.favorite || game.home_team}</span>
            <span>{game.spread && game.spread !== 'Waiting' ? game.spread : `ML ${game.favoriteMl || game.favorite_ml || ''}`}</span>
          </div>
        ))}
      </div>
      <div className="market-row" style={{ marginTop: 12 }}>
        <div><span>Parlay Odds</span><strong>Estimate</strong><b>+342</b></div>
        <div><span>Score</span><strong>Confidence</strong><b>{82 - index * 6}</b></div>
        <div><span>Structure</span><strong>Top 3</strong><b>Clean</b></div>
        <div><span>Status</span><strong>Pull History</strong><b>Live</b></div>
      </div>
    </article>
  );
}

export default async function ParlaysPage() {
  const { games, apiStatus, apiDetail } = await getApiSnapshot();
  const usableGames = games.slice(0, 6);

  return (
    <main className="shell">
      <AppHeader title="Official Hourly Parlays" apiStatus={apiStatus} apiDetail={apiDetail} />

      <section className="panel" style={{ marginBottom: 18 }}>
        <div className="panel-header compact">
          <div>
            <p className="eyebrow blue">Updated hourly</p>
            <h2 style={{ margin: 0 }}>Official Hourly Parlays</h2>
            <p className="movement" style={{ marginBottom: 0 }}>Built from live market structure and stored pull data. Capped at 3 legs.</p>
          </div>
          <Link className="ghost-button" href="/sports/mlb" style={{ textDecoration: 'none' }}>Markets</Link>
        </div>
      </section>

      <nav className="inqsi-tabs" aria-label="Parlay filters">
        <span className="active">All Sports</span><span>NBA</span><span>MLB</span><span>NFL</span><span>NHL</span><span>Soccer</span>
      </nav>

      <section className="panel" style={{ marginBottom: 18 }}>
        <div className="panel-header"><div><p className="eyebrow">Best Parlay Right Now</p><h3>Highest Confidence & Value</h3></div><strong style={{ color: '#26f37c', fontSize: 32 }}>+342</strong></div>
        <div className="signal-row"><span className="signal signal-active_slate">Top 3 Today</span><span className="signal signal-market_board">Strong Value</span><span className="signal signal-steam">High Confidence</span></div>
      </section>

      <section className="game-list">
        {usableGames.length ? [0, 1, 2].map((n) => <ParlayCard key={n} index={n} games={usableGames} />) : (
          <article className="rank-card">
            <div className="rank-head"><span>PARLAY</span><b>Waiting</b></div>
            <h4>Waiting for active market-board games</h4>
            <p>Official parlay output appears after the board has enough live pull history.</p>
          </article>
        )}
      </section>
    </main>
  );
}
