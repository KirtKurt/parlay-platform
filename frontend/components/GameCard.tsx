import Link from 'next/link';
import { SignalPill } from '@/components/SignalPill';
import { getSportSlugForLeague } from '@/lib/sports';
import { american, formatKickoff } from '@/lib/gameWhen';

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
  predicted_winner?: string;
  predicted_side?: string;
  prediction_confidence?: number | string;
  prediction_note?: string;
};

function formatOdds(value: number | string | undefined) {
  if (value === undefined || value === null || value === '') return 'Waiting';
  const numeric = Number(value);
  if (!Number.isNaN(numeric)) return american(numeric) || 'Waiting';
  return String(value);
}

export function GameCard({ game }: { game: GameLike }) {
  const id = game.id || game.game_id || 'game-waiting';
  const league = game.league || game.sport_key || 'SPORT';
  const kickoff = formatKickoff(game.start || game.commence_time) || 'Waiting';
  const matchup = game.matchup || `${game.away_team || 'Away'} @ ${game.home_team || 'Home'}`;
  const dataStatus = game.dataStatus || game.status_label || 'Pending';
  const signals = game.signals?.length ? game.signals : game.primary_signal ? [game.primary_signal] : [];
  const favorite = game.favorite || '';
  const favoriteOdds = formatOdds(game.favoriteMl ?? game.favorite_ml);
  const lean = game.predicted_winner || game.predicted_side || '';

  return (
    <article className="game-card">
      <div className="game-topline">
        <Link className="league-chip" href={`/sports/${getSportSlugForLeague(league)}`} style={{ textDecoration: 'none' }}>{league}</Link>
        <span>{kickoff}</span>
        <span className={`data-status ${String(dataStatus).toLowerCase()}`}>{dataStatus}</span>
      </div>
      <h4><Link href={`/game/${id}`} style={{ color: 'inherit', textDecoration: 'none' }}>{matchup}</Link></h4>
      <div className="market-row">
        <div>
          <span>Market favorite</span>
          <strong>{favorite || 'Waiting'}</strong>
          <b>{favorite ? favoriteOdds : 'Waiting'}</b>
        </div>
        <div>
          <span>Predicted winner</span>
          <strong>{lean || favorite || 'Waiting'}</strong>
          <b>{lean && lean !== favorite ? 'InQsi lean' : favorite ? 'From live ML' : 'Waiting'}</b>
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
      <p className="movement">
        {kickoff !== 'Waiting' ? `Starts ${kickoff}. ` : 'Kickoff waiting. '}
        {favorite ? `${favorite} is the live moneyline favorite${favoriteOdds !== 'Waiting' ? ` at ${favoriteOdds}` : ''}. ` : 'No live favorite yet. '}
        {lean && lean !== favorite ? `InQsi lean: ${lean}${game.prediction_confidence ? ` (${game.prediction_confidence})` : ''}. ` : ''}
        {game.movement || game.what_looks_wrong || ''}
      </p>
      <div className="signal-row">
        {signals.map((signal) => <SignalPill signal={signal} key={`${id}-${signal}`} />)}
      </div>
      {game.marketNote && <p className="movement">{game.marketNote}</p>}
    </article>
  );
}
