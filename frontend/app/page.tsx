import type { Metadata } from 'next';
import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { GameCard } from '@/components/GameCard';
import { getApiSnapshot } from '@/lib/api';
import { sports as sportNav } from '@/lib/sports';

export const dynamic = 'force-dynamic';

export const metadata: Metadata = {
  title: 'Live Markets, Official Parlays & AI Slip Scanner',
  description: 'InQsi shows live market data, moneyline, spread, over/under, official parlay structure, and AI slip scanning across supported sports.',
  alternates: { canonical: '/' }
};

function EmptyMarketCard() {
  return (
    <article className="game-card">
      <div className="game-topline"><span className="league-chip">SYNCING</span><span className="data-status">Waiting</span></div>
      <h4>Waiting for active-slate games</h4>
      <p className="movement">Live active-slate games appear here as soon as the backend exposes them through the market-board route.</p>
    </article>
  );
}

export default async function Home() {
  const { games, apiStatus, apiDetail } = await getApiSnapshot();
  const previewGames = games.slice(0, 8);
  const hasMarketData = previewGames.length > 0;

  return (
    <main className="inqsi-shell board-shell">
      <AppHeader eyebrow="InQsi" title="Market Intelligence" apiStatus={apiStatus} apiDetail={apiDetail} />

      <nav className="inqsi-tabs" aria-label="Sports navigation">
        {sportNav.map((sport) => <Link key={sport.slug} href={`/sports/${sport.slug}`}>{sport.label}</Link>)}
      </nav>

      <section className="board-snapshot">
        <div className="board-snapshot-head">
          <h2>Live games</h2>
          <Link className="ghost-button" href="/sports">All boards</Link>
        </div>
        <div className="inqsi-game-list">
          {hasMarketData ? previewGames.map((game) => <GameCard game={game} key={game.id} />) : <EmptyMarketCard />}
        </div>
      </section>

      <nav className="board-action-pills" aria-label="Quick tools">
        <Link href="/parlays">Official Parlays</Link>
        <Link href="/parlay-scanner">Scan My Slip</Link>
        <Link href="/register">Start Membership</Link>
      </nav>
    </main>
  );
}
