import type { Metadata } from 'next';
import Link from 'next/link';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { GameCard } from '@/components/GameCard';
import { sportVisuals } from '@/components/SportVisuals';
import { sports } from '@/lib/sports';

export const metadata: Metadata = {
  title: 'Sports Market Board',
  description: 'Live sports market board with moneyline, spread, over/under, and kickoff times across every supported sport.',
  alternates: { canonical: '/sports' }
};

export default async function SportsPage() {
  const { games, apiStatus, apiDetail } = await getApiSnapshot();
  const activeGames = games.slice(0, 8);

  return (
    <main className="shell board-shell">
      <AppHeader title="Sports Market Board" apiStatus={apiStatus} apiDetail={apiDetail} />

      <nav className="board-sport-chips" aria-label="Sports boards">
        <Link className="board-sport-chip active" href="/sports">
          <span aria-hidden="true">◎</span>
          <small>All</small>
        </Link>
        {sports.map((sport) => (
          <Link className="board-sport-chip" href={`/sports/${sport.slug}`} key={sport.slug}>
            <span aria-hidden="true">{sportVisuals[sport.slug].equipment}</span>
            <small>{sport.label}</small>
          </Link>
        ))}
      </nav>

      <section className="board-promo-strip" aria-label="InQsi tools">
        <Link className="board-promo-card" href="/parlay-scanner">
          <span>Scan</span>
          <strong>AI Slip Scanner</strong>
          <small>Review a slip before lock-in</small>
        </Link>
        <Link className="board-promo-card" href="/parlays/build">
          <span>Build</span>
          <strong>3-Leg Builder</strong>
          <small>Cap stays at three legs</small>
        </Link>
        <Link className="board-promo-card" href="/line-movement-guide">
          <span>Market</span>
          <strong>Line Movement</strong>
          <small>See what the board is warning</small>
        </Link>
      </section>

      <nav className="board-action-pills" aria-label="Quick tools">
        <Link href="/parlay-scanner">Scan</Link>
        <Link href="/parlays/build">Build</Link>
        <Link href="/parlays">Official Parlays</Link>
        <Link href="/account">Account</Link>
      </nav>

      <section className="panel board-snapshot">
        <div className="panel-header">
          <div>
            <p className="eyebrow">Live Snapshot</p>
            <h3>All active markets</h3>
          </div>
          <span className="data-status">{apiStatus === 'CONNECTED' ? 'Live' : 'Syncing'}</span>
        </div>
        <div className="game-list">
          {activeGames.length ? activeGames.map((game) => <GameCard game={game} key={game.id} />) : (
            <article className="game-card">
              <div className="game-topline"><span className="league-chip">SYNCING</span><span className="data-status">Waiting</span></div>
              <h4>Waiting for market-board data</h4>
              <p className="movement">Active games will show moneyline, spread, over/under, start time, and market signal status.</p>
            </article>
          )}
        </div>
      </section>
    </main>
  );
}
