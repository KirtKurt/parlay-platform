import Link from 'next/link';
import { SignalPill } from '@/components/SignalPill';
import { getSportSlugForLeague } from '@/lib/sports';
import type { Game } from '@/lib/mockData';

export function GameCard({ game }: { game: Game }) {
  return (
    <article className="game-card">
      <div className="game-topline">
        <Link className="league-chip" href={`/sports/${getSportSlugForLeague(game.league)}`} style={{ textDecoration: 'none' }}>{game.league}</Link>
        <span>{game.start}</span>
        <span className={`data-status ${game.dataStatus.toLowerCase()}`}>{game.dataStatus}</span>
      </div>
      <h4><Link href={`/game/${game.id}`} style={{ color: 'inherit', textDecoration: 'none' }}>{game.matchup}</Link></h4>
      <div className="market-row">
        <div>
          <span>Favorite</span>
          <strong>{game.favorite}</strong>
          <b>{game.favoriteMl}</b>
        </div>
        <div>
          <span>Underdog</span>
          <strong>{game.underdog}</strong>
          <b>{game.underdogMl > 0 ? `+${game.underdogMl}` : game.underdogMl}</b>
        </div>
        <div>
          <span>Total</span>
          <strong>O/U</strong>
          <b>{game.total}</b>
        </div>
      </div>
      <p className="movement">{game.movement}</p>
      <div className="signal-row">
        {game.signals.map((signal) => <SignalPill signal={signal} key={`${game.id}-${signal}`} />)}
      </div>
      {game.marketNote && <p className="movement">{game.marketNote}</p>}
    </article>
  );
}
