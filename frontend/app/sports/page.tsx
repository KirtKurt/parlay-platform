import Link from 'next/link';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { GameCard } from '@/components/GameCard';
import { sports } from '@/lib/sports';

export default async function SportsPage() {
  const { games, apiStatus, apiDetail } = await getApiSnapshot();
  const activeGames = games.slice(0, 8);

  return (
    <main className="shell">
      <AppHeader title="Sports Market Board" apiStatus={apiStatus} apiDetail={apiDetail} />

      <section className="panel" style={{ marginBottom: 18 }}>
        <div className="panel-header compact">
          <div>
            <p className="eyebrow blue">All sports included</p>
            <h2 style={{ margin: 0 }}>Sports Market Board</h2>
            <p className="movement" style={{ marginBottom: 0 }}>One membership opens every supported sport.</p>
          </div>
          <span className="data-status">{apiStatus === 'CONNECTED' ? 'Live' : 'Syncing'}</span>
        </div>
      </section>

      <nav className="inqsi-tabs" aria-label="Sports boards">
        {sports.map((sport) => <Link href={`/sports/${sport.slug}`} key={sport.slug}>{sport.label}</Link>)}
      </nav>

      <section className="status-row">
        <article className="status-card"><span>Active Games</span><strong>{activeGames.length}</strong><p>Visible games from the board feed.</p></article>
        <article className="status-card"><span>Markets</span><strong>ML / Spread / O-U</strong><p>Core markets visible on every game card.</p></article>
        <article className="status-card"><span>Sports</span><strong>{sports.length}</strong><p>Supported boards in one membership.</p></article>
        <article className="status-card"><span>Status</span><strong>{apiStatus === 'CONNECTED' ? 'Live' : 'Syncing'}</strong><p>{apiDetail}</p></article>
      </section>

      <section className="panel">
        <div className="panel-header"><div><p className="eyebrow">Live Snapshot</p><h3>All active markets</h3></div><Link className="ghost-button" href="/parlays">Parlays</Link></div>
        <div className="game-list">
          {activeGames.length ? activeGames.map((game) => <GameCard game={game} key={game.id} />) : (
            <article className="game-card"><div className="game-topline"><span className="league-chip">SYNCING</span><span className="data-status">Waiting</span></div><h4>Waiting for market-board data</h4><p className="movement">Active games will show moneyline, spread, over/under, start time, and market signal status.</p></article>
          )}
        </div>
      </section>
    </main>
  );
}
