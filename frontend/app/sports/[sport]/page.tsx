import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { GameCard } from '@/components/GameCard';
import { getSportBySlug, getSportSlugForLeague, sports } from '@/lib/sports';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export function generateStaticParams() {
  return sports.map((sport) => ({ sport: sport.slug }));
}

export default async function SportPage({ params }: { params: { sport: string } }) {
  const sport = getSportBySlug(params.sport);
  if (!sport) notFound();

  const { games, rankings, apiStatus, apiDetail } = await getApiSnapshot(sport.slug);
  const visibleGames = games.filter((game) => getSportSlugForLeague(game.league || game.sport_key) === sport.slug);
  const hasMarketData = visibleGames.length > 0;
  const hasRankings = Boolean(rankings?.length);
  const nowLabel = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York', timeZoneName: 'short' }).format(new Date());
  const statusDetail = apiDetail && /https?:\/\//.test(apiDetail) ? 'Waiting on live board games' : (apiDetail || 'Market board is loading.');

  return (
    <main className="shell">
      <AppHeader title={sport.title} apiStatus={apiStatus} apiDetail={statusDetail} />

      <nav className="inqsi-tabs" aria-label="Sports market navigation">
        {sports.map((item) => <Link className={item.slug === sport.slug ? 'active' : ''} href={`/sports/${item.slug}`} key={item.slug}>{item.label}</Link>)}
      </nav>

      <section className="panel" style={{ marginBottom: 18 }}>
        <div className="panel-header compact">
          <div>
            <p className="eyebrow blue">Live markets</p>
            <h2 style={{ margin: 0 }}>{sport.label} Market Board</h2>
            <p className="movement" style={{ marginBottom: 0 }}>{hasMarketData ? `${visibleGames.length} active game${visibleGames.length === 1 ? '' : 's'} with ML, spread, and total.` : 'Waiting for live board games.'}</p>
          </div>
          <span className="data-status">Updated {nowLabel}</span>
        </div>
      </section>

      <section className="status-row">
        <article className="status-card"><span>Active Games</span><strong>{visibleGames.length}</strong><p>Games currently available in this sport window.</p></article>
        <article className="status-card"><span>Markets</span><strong>ML / Spread / O-U</strong><p>Core markets are shown directly on each game card.</p></article>
        <article className="status-card"><span>Data Status</span><strong>{apiStatus === 'CONNECTED' ? 'Live' : 'Syncing'}</strong><p>{statusDetail}</p></article>
        <article className="status-card"><span>Membership</span><strong>All sports</strong><p>One membership includes every supported sport.</p></article>
      </section>

      <section className="content-grid">
        <div className="panel slate-panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Sports Market Board</p>
              <h3>{hasMarketData ? 'Live Snapshot' : 'Waiting'}</h3>
            </div>
            <Link className="ghost-button" href="/parlays" style={{ textDecoration: 'none' }}>Official Parlays</Link>
          </div>
          <div className="game-list">
            {hasMarketData ? visibleGames.map((game) => <GameCard game={game} key={game.id} />) : (
              <article className="game-card">
                <div className="game-topline"><span className="league-chip">{sport.label}</span><span className="data-status">Syncing</span></div>
                <h4>Waiting</h4>
                <p className="movement">When the backend has games inside the live window, this page will show the teams, start time, moneyline, spread, total, book count, and radar tags here.</p>
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
              <div className="rank-head"><span>Readiness</span><b>{hasRankings ? 'READY' : 'WAITING'}</b></div>
              <h4>{hasRankings ? 'Ranked output available' : 'Waiting for 12-pull readiness'}</h4>
              <p>{hasRankings ? (rankings?.[0]?.note || 'Live ranking note') : 'Official parlay builds wait for enough pull history. No forced picks.'}</p>
            </article>
            <article className="rank-card">
              <div className="rank-head"><span>Build Rule</span><b>{hasRankings ? 'TOP-3' : 'WAITING'}</b></div>
              <h4>{hasRankings ? (rankings?.[0]?.structure || 'Waiting') : 'Waiting'}</h4>
              <p>{hasRankings ? 'Structure comes from the hourly builder only.' : 'No sample structure until the hourly builder publishes.'}</p>
            </article>
          </div>
        </aside>
      </section>
    </main>
  );
}
