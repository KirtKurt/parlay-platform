import Link from 'next/link';
import { RadarStrip } from '@/components/RadarStrip';
import { getSportSlugForLeague } from '@/lib/sports';
import { formatAmericanOdds, formatKickoff, gamePath, impliedPercent } from '@/lib/kickoff';
import { radarFromGame } from '@/lib/radarSignals';

type GameLike = {
  id?: string;
  game_id?: string;
  league?: string;
  sport_key?: string;
  start?: string;
  commence_time?: string;
  matchup?: string;
  home_team?: string;
  away_team?: string;
  favorite?: string;
  underdog?: string;
  favoriteMl?: number | string;
  favorite_ml?: number | string;
  underdogMl?: number | string;
  underdog_ml?: number | string;
  spread?: number | string;
  total?: number | string;
  movement?: string;
  what_looks_wrong?: string;
  status_label?: string;
  signals?: Array<string>;
  primary_signal?: string;
  dataStatus?: string;
  marketNote?: string;
  bookCount?: number;
};

function formatOdds(value: number | string | undefined) {
  if (value === undefined || value === null || value === '') return 'Waiting';
  const numeric = Number(value);
  if (!Number.isNaN(numeric)) return numeric > 0 ? `+${numeric}` : `${numeric}`;
  return String(value);
}

function customerMovement(game: GameLike) {
  const raw = String(game.movement || game.what_looks_wrong || '');
  if (!raw || /active[\s_]?slate/i.test(raw) || raw.toUpperCase() === 'ACTIVE_SLATE') {
    return game.bookCount
      ? `Live board · ${game.bookCount} books quoting`
      : 'Waiting on verified market movement.';
  }
  return raw;
}

export function GameCard({ game }: { game: GameLike }) {
  const league = game.league || game.sport_key || 'SPORT';
  const start = formatKickoff(game.start || game.commence_time);
  const matchup = game.matchup || `${game.away_team || 'Away'} @ ${game.home_team || 'Home'}`;
  const dataStatus = game.dataStatus || game.status_label || 'Pending';
  const favoriteOdds = game.favoriteMl ?? game.favorite_ml;
  const implied = impliedPercent(favoriteOdds);
  const href = gamePath({ ...game, matchup });
  const radar = radarFromGame({
    ...game,
    signals: (game.signals || []).filter((s) => String(s).toUpperCase() !== 'ACTIVE_SLATE'),
    primary_signal: game.primary_signal === 'ACTIVE_SLATE' ? 'MARKET_BOARD' : game.primary_signal
  }).slice(0, 4);

  return (
    <article className="game-card">
      <div className="game-topline">
        <Link className="league-chip" href={`/sports/${getSportSlugForLeague(league)}`} style={{ textDecoration: 'none' }}>{league}</Link>
        <span>{start}</span>
        <span className={`data-status ${String(dataStatus).toLowerCase()}`}>{dataStatus}</span>
      </div>
      <h4><Link href={href} style={{ color: 'inherit', textDecoration: 'none' }}>{matchup}</Link></h4>
      <p className="movement">
        {game.favorite
          ? `Market favorite: ${game.favorite} ${formatAmericanOdds(favoriteOdds) || 'Waiting'}${implied ? ` · ${implied}% implied` : ''}`
          : 'Waiting for a market favorite.'}
      </p>
      <div className="market-row">
        <div>
          <span>Favorite</span>
          <strong>{game.favorite || game.home_team || 'Waiting'}</strong>
          <b>{formatOdds(favoriteOdds)}</b>
        </div>
        <div>
          <span>Underdog</span>
          <strong>{game.underdog || game.away_team || 'Waiting'}</strong>
          <b>{formatOdds(game.underdogMl ?? game.underdog_ml)}</b>
        </div>
        <div>
          <span>Spread</span>
          <strong>Line</strong>
          <b>{game.spread ?? 'Waiting'}</b>
        </div>
        <div>
          <span>Total</span>
          <strong>O/U</strong>
          <b>{game.total ?? 'Waiting'}</b>
        </div>
      </div>
      <p className="movement">{customerMovement(game)}</p>
      <RadarStrip items={radar} title="On our radar" />
      {game.marketNote && <p className="movement">{game.marketNote}</p>}
    </article>
  );
}
