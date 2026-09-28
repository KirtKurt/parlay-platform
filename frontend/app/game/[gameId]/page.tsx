import Link from 'next/link';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { RadarStrip } from '@/components/RadarStrip';
import { LineMovementGraph } from '@/components/LineMovementGraph';
import { KickoffLabel } from '@/components/KickoffLabel';
import { findGame } from '@/lib/findGame';
import { formatAmericanOdds, impliedPercent } from '@/lib/kickoff';
import { visitorTimeZone } from '@/lib/visitorTimeZone';
import { getSportSlugForLeague } from '@/lib/sports';
import { radarFromGame } from '@/lib/radarSignals';

export default async function GameDetailPage({ params }: { params: { gameId: string } }) {
  const { games, lineMovement, apiStatus, apiDetail } = await getApiSnapshot();
  const game = findGame(games, params.gameId);
  const serverTimeZone = visitorTimeZone();
  const kickoffValue = game?.start || game?.commence_time;
  const favoriteOdds = game?.favoriteMl ?? game?.favorite_ml;
  const implied = impliedPercent(favoriteOdds);
  const sportSlug = getSportSlugForLeague(game?.league || game?.sport_key || 'mlb');
  const radar = radarFromGame(game || {});

  return (
    <main className="shell">
      <AppHeader title="Game Detail" apiStatus={apiStatus} apiDetail={apiDetail} />

      <section className="panel" style={{ marginBottom: 18 }}>
        <div className="game-topline"><span className="league-chip">{game?.league || 'SPORT'}</span><KickoffLabel value={kickoffValue} serverTimeZone={serverTimeZone} /><span className="data-status">{game ? (game.status_label || 'Live') : 'Waiting'}</span></div>
        <h2 style={{ marginBottom: 12 }}>{game?.matchup || 'Waiting on this matchup'}</h2>
        <p className="movement">
          {game?.favorite
            ? `Market favorite: ${game.favorite} ${formatAmericanOdds(favoriteOdds) || 'Waiting'}${implied ? ` · ${implied}% implied` : ''}`
            : 'Waiting for a live market favorite. InQsi is not inventing a winner.'}
        </p>
        <p className="movement">{game?.movement || 'This game is not on the current live board.'}</p>
        <RadarStrip items={radar} title="On our radar" />
        <div className="hero-actions">
          <Link className="ghost-button" href={`/sports/${sportSlug}`} style={{ textDecoration: 'none' }}>Back to Market Board</Link>
          <Link className="inqsi-primary" href="/parlays" style={{ textDecoration: 'none' }}>Build With This Game</Link>
        </div>
      </section>

      <section className="panel" style={{ marginBottom: 18 }}>
        <div className="panel-header"><div><p className="eyebrow">Live Market Snapshot</p><h3>Moneyline, spread, and total</h3></div></div>
        <div className="market-row">
          <div><span>Favorite</span><strong>{game?.favorite || 'Waiting'}</strong><b>{formatAmericanOdds(favoriteOdds) || 'Waiting'}</b></div>
          <div><span>Underdog</span><strong>{game?.underdog || 'Waiting'}</strong><b>{formatAmericanOdds(game?.underdogMl ?? game?.underdog_ml) || 'Waiting'}</b></div>
          <div><span>Spread</span><strong>Line</strong><b>{game?.spread || 'Waiting'}</b></div>
          <div><span>Total</span><strong>O/U</strong><b>{game?.total || 'Waiting'}</b></div>
        </div>
      </section>

      <section className="status-row">
        <article className="status-card"><span>Kickoff</span><strong><KickoffLabel value={kickoffValue} serverTimeZone={serverTimeZone} /></strong><p>Shown in your local timezone from the live board.</p></article>
        <article className="status-card"><span>Favorite</span><strong>{game?.favorite || 'Waiting'}</strong><p>Market favorite from live moneyline.</p></article>
        <article className="status-card"><span>Book Count</span><strong>{game?.bookCount || 'Waiting'}</strong><p>Market sources represented.</p></article>
        <article className="status-card"><span>Risk</span><strong>{game?.risk || 'Waiting'}</strong><p>{game?.confidence || 'Waiting on live board data.'}</p></article>
      </section>

      <section className="content-grid" style={{ marginTop: 18 }}>
        <div className="panel">
          <div className="panel-header"><div><p className="eyebrow">Market Signals</p><h3>On our radar</h3></div></div>
          <RadarStrip items={radar} />
          <p className="movement">{game?.marketNote ?? 'Signals reflect market movement only. They do not guarantee outcomes.'}</p>
        </div>
        <aside className="panel">
          <p className="eyebrow">Build Gate</p>
          <h3>Eligibility review</h3>
          <p className="movement">Eligible only if the rest of the slate preserves anchor discipline and avoids forced confidence.</p>
          <Link className="inqsi-primary" href="/parlays" style={{ textDecoration: 'none', width: '100%' }}>Open Parlays</Link>
        </aside>
      </section>

      <LineMovementGraph data={lineMovement} />
    </main>
  );
}
