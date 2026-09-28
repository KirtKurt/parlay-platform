import Link from 'next/link';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { formatKickoff } from '@/lib/kickoff';

export const dynamic = 'force-dynamic';

function isSampleParlay(row: any) {
  const blob = JSON.stringify(row || {}).toLowerCase();
  return blob.includes('+342') || blob.includes('"342"') || /confidence"?:\s*8[0-2]/.test(blob) || blob.includes('celtics') || blob.includes('sample');
}

export default async function ParlaysPage() {
  const { games, rankings, apiStatus, apiDetail } = await getApiSnapshot();
  const official = (Array.isArray(rankings) ? rankings : []).filter((row) => !isSampleParlay(row));
  const liveCount = games.length;

  return (
    <main className="shell">
      <AppHeader title="Official Hourly Parlays" apiStatus={apiStatus} apiDetail={apiDetail} />
      <section className="panel" style={{ marginBottom: 18 }}>
        <div className="panel-header compact">
          <div>
            <p className="eyebrow blue">Updated hourly</p>
            <h2 style={{ margin: 0 }}>Official Hourly Parlays</h2>
            <p className="movement" style={{ marginBottom: 0 }}>Built from live market structure. Combined odds stay hidden until the hourly builder publishes a real price.</p>
          </div>
          <Link className="ghost-button" href="/sports" style={{ textDecoration: 'none' }}>Markets</Link>
        </div>
      </section>
      <nav className="inqsi-tabs" aria-label="Parlay filters">
        <Link href="/sports">All Sports</Link>
        <Link href="/sports/nba">NBA</Link>
        <Link href="/sports/mlb">MLB</Link>
        <Link href="/sports/nfl">NFL</Link>
        <Link href="/sports/nhl">NHL</Link>
        <Link href="/sports/soccer">Soccer</Link>
      </nav>
      <section className="panel" style={{ marginBottom: 18 }}>
        <div className="panel-header">
          <div>
            <p className="eyebrow">Best Parlay Right Now</p>
            <h3>{official.length ? 'Official structure available' : liveCount ? 'Waiting on official hourly odds' : 'Waiting for live board data'}</h3>
          </div>
          <strong style={{ color: '#9fb0be', fontSize: 28 }}>Waiting</strong>
        </div>
        <p className="movement">{liveCount ? `${liveCount} live board games are available. Combined parlay odds are not estimated.` : 'Official parlay output appears after the board has enough live pull history.'}</p>
      </section>
      <section className="game-list">
        {official.length ? official.slice(0, 3).map((row: any, index: number) => (
          <article className="rank-card" key={row.id || index}>
            <div className="rank-head"><span>PARLAY</span><b>Official</b></div>
            <h4>{row.structure || row.title || '3-leg official structure'}</h4>
            <p>{row.note || row.explanation || 'Published from hourly builder output.'}</p>
          </article>
        )) : games.slice(0, 8).map((game) => (
          <article className="rank-card" key={game.id}>
            <div className="rank-head"><span>{game.league}</span><b>{formatKickoff(game.start || game.commence_time) || 'Waiting'}</b></div>
            <h4>{game.matchup}</h4>
            <p>Market favorite: {game.favorite || 'Waiting'} {game.favoriteMl || game.favorite_ml || ''}.</p>
          </article>
        ))}
        {!official.length && !games.length && (
          <article className="rank-card">
            <div className="rank-head"><span>PARLAY</span><b>Waiting</b></div>
            <h4>No official 3-leg price yet</h4>
            <p>InQsi does not invent parlay odds.</p>
          </article>
        )}
      </section>
    </main>
  );
}
