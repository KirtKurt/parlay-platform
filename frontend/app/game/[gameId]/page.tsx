import Link from 'next/link';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { SignalPill } from '@/components/SignalPill';
import { LineMovementGraph } from '@/components/LineMovementGraph';
import { formatAmericanOdds, formatKickoff, impliedPercent } from '@/lib/kickoff';
import { getSportSlugForLeague } from '@/lib/sports';

function slug(value?: string) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

function findGame(games: Awaited<ReturnType<typeof getApiSnapshot>>['games'], gameId: string) {
  const id = decodeURIComponent(gameId).toLowerCase();
  return games.find((game) => {
    const keys = [
      game.id,
      game.game_id,
      slug(game.matchup),
      slug(`${game.away_team}-${game.home_team}`),
      slug(`${game.home_team}-${game.away_team}`),
      slug(`${game.league}-${game.away_team}-${game.home_team}`),
      slug(`${game.sport_key}-${game.away_team}-${game.home_team}`),
    ].map((value) => String(value || '').toLowerCase());
    if (keys.includes(id)) return true;
    return Boolean(slug(game.home_team) && slug(game.away_team) && id.includes(slug(game.home_team)) && id.includes(slug(game.away_team)));
  });
}

export default async function GameDetailPage({ params }: { params: { gameId: string } }) {
  const { games, lineMovement, apiStatus, apiDetail } = await getApiSnapshot();
  const game = findGame(games, params.gameId);
  const start = formatKickoff(game?.start || game?.commence_time);
  const favoriteOdds = game?.favoriteMl ?? game?.favorite_ml;
  const implied = impliedPercent(favoriteOdds);
  const sportSlug = getSportSlugForLeague(game?.league || game?.sport_key || 'mlb');

  return (
    <main className="shell">
      <AppHeader title="Game Detail" apiStatus={apiStatus} apiDetail={apiDetail} />

      <section className="panel" style={{ marginBottom: 18 }}>
        <div className="game-topline"><span className="league-chip">{game?.league || 'SPORT'}</span><span>{start}</span><span className="data-status">{game ? (game.status_label || 'Live') : 'Waiting'}</span></div>
        <h2 style={{ marginBottom: 12 }}>{game?.matchup || decodeURIComponent(params.gameId)}</h2>
        <p className="movement">
          {game?.favorite
            ? `Market favorite: ${game.favorite} ${formatAmericanOdds(favoriteOdds) || 'Waiting'}${implied ? ` · ${implied}% implied` : ''}`
            : 'Waiting for a live market favorite. InQsi is not inventing a winner.'}
        </p>
        <p className="movement">{game?.movement || 'This game is not on the current live board.'}</p>
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
        <article className="status-card"><span>Kickoff</span><strong>{start}</strong><p>Eastern time from the live board.</p></article>
        <article className="status-card"><span>Favorite</span><strong>{game?.favorite || 'Waiting'}</strong><p>Market favorite from live moneyline.</p></article>
        <article className="status-card"><span>Book Count</span><strong>{game?.bookCount || 'Waiting'}</strong><p>Market sources represented.</p></article>
        <article className="status-card"><span>Risk</span><strong>{game?.risk || 'Waiting'}</strong><p>{game?.confidence || 'Waiting on live board data.'}</p></article>
      </section>

      <section className="content-grid" style={{ marginTop: 18 }}>
        <div className="panel">
          <div className="panel-header"><div><p className="eyebrow">Market Signals</p><h3>Signal types detected</h3></div></div>
          <div className="signal-row" style={{ marginBottom: 14 }}>
            {(game?.signals || []).map((signal) => <SignalPill signal={signal} key={signal} />)}
          </div>
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
