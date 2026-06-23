import type { Metadata } from 'next';
import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { GameCard } from '@/components/GameCard';
import { getApiSnapshot } from '@/lib/api';
import { sports as sportNav } from '@/lib/sports';

export const metadata: Metadata = {
  title: 'InQsi | Live Markets, Official Parlays & AI Slip Scanner',
  description: 'InQsi shows live market data, moneyline, spread, over/under, official parlay structure, and AI slip scanning across supported sports.',
  alternates: { canonical: '/' }
};

function EmptyMarketCard() {
  return (
    <article className="game-card">
      <div className="game-topline"><span className="league-chip">SYNCING</span><span className="data-status">Waiting</span></div>
      <h4>Waiting for active-slate games</h4>
      <div className="market-row">
        <div><span>ML</span><strong>Moneyline</strong><b>Waiting</b></div>
        <div><span>Spread</span><strong>Line</strong><b>Waiting</b></div>
        <div><span>Total</span><strong>O/U</strong><b>Waiting</b></div>
        <div><span>Books</span><strong>Market Board</strong><b>Syncing</b></div>
      </div>
      <p className="movement">Live active-slate games appear here as soon as the backend exposes them through the market-board route.</p>
    </article>
  );
}

export default async function Home() {
  const { games, apiStatus, apiDetail } = await getApiSnapshot();
  const activeGames = games.slice(0, 6);
  const hasMarketData = activeGames.length > 0;
  const nowLabel = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' }).format(new Date());

  return (
    <main className="inqsi-shell">
      <AppHeader eyebrow="InQsi" title="Market Intelligence" apiStatus={apiStatus} apiDetail={apiDetail} />

      <section className="inqsi-hero" id="main-content">
        <div className="inqsi-hero-card">
          <p className="inqsi-promo">Live market intelligence</p>
          <h1>Find out if you are <span>wrong.</span></h1>
          <p>Live market intelligence, official 3-leg parlays, smarter slip building, and AI slip scanning across every supported sport.</p>
          <div className="hero-actions">
            <Link className="inqsi-primary" href="/register">Start Membership</Link>
            <Link className="ghost-button" href="/sports/mlb">View Market Board</Link>
          </div>
        </div>
        <aside className="inqsi-signup-card">
          <h3>One membership. All sports included.</h3>
          <p>Membership includes NFL, CFB, NBA, NCAAM, MLB, WNBA, NHL, Soccer, Tennis, and future supported sports.</p>
          <Link className="inqsi-primary" href="/register">Create Account</Link>
          <Link href="/login">Login</Link>
        </aside>
      </section>

      <nav className="inqsi-tabs" aria-label="Sports navigation">
        {sportNav.map((sport) => <Link key={sport.slug} href={`/sports/${sport.slug}`}>{sport.label}</Link>)}
      </nav>

      <section className="status-row">
        <article className="status-card"><span>Active Games</span><strong>{activeGames.length}</strong><p>Showing live active-slate board games.</p></article>
        <article className="status-card"><span>Sports Live</span><strong>{new Set(activeGames.map((g) => g.sport_key)).size}</strong><p>Sports with visible board data.</p></article>
        <article className="status-card"><span>Fresh Pull</span><strong>{nowLabel}</strong><p>Frontend render time.</p></article>
        <article className="status-card"><span>Status</span><strong>{apiStatus === 'CONNECTED' ? 'Live' : 'Syncing'}</strong><p>{apiDetail}</p></article>
      </section>

      <section className="inqsi-layout">
        <div>
          <section className="inqsi-panel">
            <div className="inqsi-section-head">
              <div>
                <p className="eyebrow">Live Snapshot</p>
                <h2>Sports Market Board</h2>
              </div>
              <Link className="ghost-button" href="/sports/mlb">View All</Link>
            </div>
            <div className="inqsi-game-list">
              {hasMarketData ? activeGames.map((game) => <GameCard game={game} key={game.id} />) : <EmptyMarketCard />}
            </div>
          </section>
        </div>
        <aside className="inqsi-panel">
          <div className="inqsi-section-head"><h2>Official Hourly Parlays</h2><span>Top-3 discipline</span></div>
          <p className="movement">Official parlay builds use stored pull history. No forced picks. No two-coin-flip builds.</p>
          <Link className="inqsi-primary" href="/parlays" style={{ textDecoration: 'none', width: '100%' }}>Open Parlays</Link>
          <div style={{ height: 14 }} />
          <div className="inqsi-section-head"><h2>Slip Scanner</h2><span>Bring your picks</span></div>
          <p className="movement">Scan an existing slip and review weak legs, market alignment, and risk before lock-in.</p>
          <Link className="ghost-button" href="/parlay-scanner" style={{ textDecoration: 'none', width: '100%' }}>Scan My Slip</Link>
        </aside>
      </section>
    </main>
  );
}
