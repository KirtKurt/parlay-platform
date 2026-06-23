import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { GameCard } from '@/components/GameCard';
import { getSportBySlug, getSportSlugForLeague, sports } from '@/lib/sports';

export function generateStaticParams() {
  return sports.map((sport) => ({ sport: sport.slug }));
}

export default async function SportPage({ params }: { params: { sport: string } }) {
  const sport = getSportBySlug(params.sport);
  if (!sport) notFound();

  const { games, rankings, apiStatus, apiDetail } = await getApiSnapshot(sport.slug);
  const visibleGames = games.filter((game) => getSportSlugForLeague(game.league || game.sport_key) === sport.slug);
  const hasMarketData = visibleGames.length > 0;
  const nowLabel = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' }).format(new Date());

  return (
    <main className="shell">
      <AppHeader title={sport.title} apiStatus={apiStatus} apiDetail={apiDetail} />

      <nav className="inqsi-tabs" aria-label="Sports market navigation">
        {sports.map((item) => <Link className={item.slug === sport.slug ? 'active' : ''} href={`/sports/${item.slug}`} key={item.slug}>{item.label}</Link>)}
      </nav>

      <section className="panel" style={{ marginBottom: 18 }}>
        <div className="panel-header compact">
          <div>
            <p className="eyebrow blue">Live markets</p>
            <h2 style={{ margin: 0 }}>{sport.label} Market Board</h2>
            <p className="movement" style={{ marginBottom: 0 }}>{hasMarketData ? `${visibleGames.length} active game${visibleGames.length === 1 ? '' : 's'} with ML, spread, and total.` : 'Waiting for active-slate games from the market board.'}</p>
          </div>
          <span className="data-status">Updated {nowLabel}</span>
        </div>
      </section>

      <section className="status-row">
        <article className="status-card"><span>Active Games</span><strong>{visibleGames.length}</strong><p>Games currently available in this sport window.</p></article>
        <article className="status-card"><span>Markets</span><strong>ML / Spread / O-U</strong><p>Core markets are shown directly on each game card.</p></article>
        <article className="status-card"><span>Data Status</span><strong>{apiStatus === 'CONNECTED' ? 'Live' : 'Syncing'}</strong><p>{apiDetail || 'Market board is loading.'}</p></article>
        <article className="status-card"><span>Membership</span><strong>All sports</strong><p>One membership includes every supported sport.</p></article>
      </section>

      <section className="content-grid">
        <div className="panel slate-panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Sports Market Board</p>
              <h3>{hasMarketData ? 'Live Snapshot' : 'No active slate yet'}</h3>
            </div>
            <Link className="ghost-button" href="/parlays" style={{ textDecoration: 'none' }}>Official Parlays</Link>
          </div>
          <div className="game-list">
            {hasMarketData ? visibleGames.map((game) => <GameCard game={game} key={game.id} />) : (
              <article className="game-card">
                <div className="game-topline"><span className="league-chip">{sport.label}</span><span className="data-status">Syncing</span></div>
                <h4>Waiting for active-slate data</h4>
                <p className="movement">When the backend has games inside the active window, this page will show the teams, start time, moneyline, spread, total, book count, and market signal tags here.</p>
              </article>
            )}
          </div>
        </div>

        <aside className="panel rank-panel">
          <div className="panel-header compact">
            <div>
              <p className="eyebrow">Official Hourly Parlays</p>
              <h3>3-leg discipline</h3>
            </div>
          </div>
          <div className="rank-list">
            <article className="rank-card top-zone">
              <div className="rank-head"><span>Readiness</span><b>{rankings?.length ? 'READY' : 'WAITING'}</b></div>
              <h4>{rankings?.length ? 'Ranked output available' : 'Waiting for 12-pull readiness'}</h4>
              <p>{rankings?.[0]?.note ?? 'Official parlay builds wait for enough pull history. No forced picks.'}</p>
            </article>
            <article className="rank-card">
              <div className="rank-head"><span>Build Rule</span><b>TOP-3</b></div>
              <h4>{rankings?.[0]?.structure ?? '2 Strong + 1 Coin Flip'}</h4>
              <p>Inqis ranks all 8 three-leg outcomes and surfaces the cleanest structures only.</p>
            </article>
          </div>
        </aside>
      </section>
    </main>
  );
}
