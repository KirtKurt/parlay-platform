import Link from 'next/link';
import { SignalPill } from '@/components/SignalPill';
import { getSportSlugForLeague } from '@/lib/sports';

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
  total?: number | string;
  movement?: string;
  what_looks_wrong?: string;
  status_label?: string;
  signals?: Array<string>;
  primary_signal?: string;
  dataStatus?: string;
  marketNote?: string;
};

function formatOdds(value: number | string | undefined) {
  if (value === undefined || value === null || value === '') return 'Waiting';
  const numeric = Number(value);
  if (!Number.isNaN(numeric)) return numeric > 0 ? `+${numeric}` : `${numeric}`;
  return String(value);
}

export function GameCard({ game }: { game: GameLike }) {
  const id = game.id || game.game_id || 'game-waiting';
  const league = game.league || game.sport_key || 'SPORT';
  const start = game.start || game.commence_time || 'TBD';
  const matchup = game.matchup || `${game.away_team || 'Away'} @ ${game.home_team || 'Home'}`;
  const dataStatus = game.dataStatus || game.status_label || 'Pending';
  const signals = game.signals?.length ? game.signals : game.primary_signal ? [game.primary_signal] : ['MARKET_ANOMALY'];

  return (
    <article className="game-card">
      <div className="game-topline">
        <Link className="league-chip" href={`/sports/${getSportSlugForLeague(league)}`} style={{ textDecoration: 'none' }}>{league}</Link>
        <span>{start}</span>
        <span className={`data-status ${dataStatus.toLowerCase()}`}>{dataStatus}</span>
      </div>
      <h4><Link href={`/game/${id}`} style={{ color: 'inherit', textDecoration: 'none' }}>{matchup}</Link></h4>
      <div className="market-row">
        <div>
          <span>Favorite</span>
          <strong>{game.favorite || game.home_team || 'Waiting'}</strong>
          <b>{formatOdds(game.favoriteMl ?? game.favorite_ml)}</b>
        </div>
        <div>
          <span>Underdog</span>
          <strong>{game.underdog || game.away_team || 'Waiting'}</strong>
          <b>{formatOdds(game.underdogMl ?? game.underdog_ml)}</b>
        </div>
        <div>
          <span>Total</span>
          <strong>O/U</strong>
          <b>{game.total ?? 'Waiting'}</b>
        </div>
      </div>
      <p className="movement">{game.movement || game.what_looks_wrong || 'Waiting on verified market movement.'}</p>
      <div className="signal-row">
        {signals.map((signal) => <SignalPill signal={signal} key={`${id}-${signal}`} />)}
      </div>
      {game.marketNote && <p className="movement">{game.marketNote}</p>}
    </article>
  );
}
