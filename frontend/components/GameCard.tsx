import Link from 'next/link';
import { RadarStrip } from '@/components/RadarStrip';
import { getTeamVisual } from '@/components/SportVisuals';
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

function parseLine(value: number | string | undefined) {
  if (value === undefined || value === null || value === '') return null;
  const match = String(value).replace(/,/g, '').match(/[+-]?\d+(?:\.\d+)?/);
  if (!match) return null;
  const numeric = Number(match[0]);
  return Number.isNaN(numeric) ? null : numeric;
}

function formatLine(value: number) {
  return value > 0 ? `+${value}` : `${value}`;
}

function splitTeams(game: GameLike) {
  if (game.away_team && game.home_team) {
    return { away: game.away_team, home: game.home_team };
  }
  const matchup = String(game.matchup || '');
  const parts = matchup.split(/\s+@\s+|\s+vs\.?\s+/i).map((part) => part.trim()).filter(Boolean);
  if (parts.length === 2) return { away: parts[0], home: parts[1] };
  return {
    away: game.underdog || 'Away',
    home: game.favorite || game.home_team || 'Home'
  };
}

function teamLooksLike(team: string, label?: string) {
  if (!label) return false;
  const left = team.toLowerCase();
  const right = label.toLowerCase();
  return left.includes(right) || right.includes(left);
}

function moneyFor(team: string, game: GameLike, fallback?: number | string) {
  if (teamLooksLike(team, game.favorite)) return game.favoriteMl ?? game.favorite_ml;
  if (teamLooksLike(team, game.underdog)) return game.underdogMl ?? game.underdog_ml;
  return fallback;
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
  const { away, home } = splitTeams(game);
  const awayVisual = getTeamVisual(away);
  const homeVisual = getTeamVisual(home);
  const spread = parseLine(game.spread);
  const total = parseLine(game.total);
  const awayIsFavorite = teamLooksLike(away, game.favorite);
  const awaySpread = spread == null ? null : awayIsFavorite ? -Math.abs(spread) : Math.abs(spread);
  const homeSpread = awaySpread == null ? null : -awaySpread;
  const radar = radarFromGame({
    ...game,
    signals: (game.signals || []).filter((s) => String(s).toUpperCase() !== 'ACTIVE_SLATE'),
    primary_signal: game.primary_signal === 'ACTIVE_SLATE' ? 'MARKET_BOARD' : game.primary_signal
  }).slice(0, 4);

  return (
    <article className="game-card board-game-card">
      <div className="game-topline">
        <Link className="league-chip" href={`/sports/${getSportSlugForLeague(league)}`} style={{ textDecoration: 'none' }}>{league}</Link>
        <span>{start}</span>
        <span className={`data-status ${String(dataStatus).toLowerCase()}`}>{dataStatus}</span>
      </div>

      <div className="board-matchup">
        <div className="board-team">
          <b>{awayVisual.abbr}</b>
          <strong>{away}</strong>
        </div>
        <span className="board-kickoff">{start || 'Waiting'}</span>
        <div className="board-team board-team-right">
          <b>{homeVisual.abbr}</b>
          <strong>{home}</strong>
        </div>
      </div>

      <p className="board-odds-caption">{awayVisual.abbr}</p>
      <div className="board-odds-grid">
        <div className="board-odds-cell"><span>Spread</span><b>{awaySpread == null ? 'Waiting' : formatLine(awaySpread)}</b></div>
        <div className="board-odds-cell"><span>Money</span><b>{formatOdds(moneyFor(away, game, awayIsFavorite ? favoriteOdds : game.underdogMl ?? game.underdog_ml))}</b></div>
        <div className="board-odds-cell"><span>Total</span><b>{total == null ? 'Waiting' : `o${total}`}</b></div>
      </div>

      <p className="board-odds-caption">{homeVisual.abbr}</p>
      <div className="board-odds-grid">
        <div className="board-odds-cell"><span>Spread</span><b>{homeSpread == null ? 'Waiting' : formatLine(homeSpread)}</b></div>
        <div className="board-odds-cell"><span>Money</span><b>{formatOdds(moneyFor(home, game, awayIsFavorite ? game.underdogMl ?? game.underdog_ml : favoriteOdds))}</b></div>
        <div className="board-odds-cell"><span>Total</span><b>{total == null ? 'Waiting' : `u${total}`}</b></div>
      </div>

      <p className="movement">
        {game.favorite
          ? `Market favorite: ${game.favorite} ${formatAmericanOdds(favoriteOdds) || 'Waiting'}${implied ? ` · ${implied}% implied` : ''}`
          : 'Waiting for a market favorite.'}
      </p>
      <p className="movement">{customerMovement(game)}</p>
      <RadarStrip items={radar} title="On our radar" />
      {game.marketNote && <p className="movement">{game.marketNote}</p>}
      <Link href={href} style={{ color: '#20e5ff', textDecoration: 'none', fontWeight: 800 }}>Open game</Link>
    </article>
  );
}
