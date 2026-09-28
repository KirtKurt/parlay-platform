import Link from 'next/link';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { GameTools } from '@/components/GameTools';
import { findGame } from '@/lib/findGame';
import { formatKickoff } from '@/lib/kickoff';
import { getSportSlugForLeague } from '@/lib/sports';

export default async function GameDetailPage({ params }: { params: { gameId: string } }) {
  const { games, predictions, rankings, apiStatus, apiDetail } = await getApiSnapshot();
  const game = findGame(games, params.gameId);
  const pred = predictions.find((row) =>
    row.game_id === game?.id ||
    row.game_id === game?.game_id ||
    (row.home_team === game?.home_team && row.away_team === game?.away_team)
  );
  const merged = game
    ? {
        ...game,
        predicted_winner: pred?.predicted_winner || (pred as { predicted_team?: string } | undefined)?.predicted_team || game.predicted_winner,
        predicted_side: pred?.predicted_side || game.predicted_side,
        short_explanation: pred?.short_explanation,
        confidence: pred?.confidence_score ?? game.confidence,
        primary_signal: pred?.primary_signal || game.primary_signal,
        signal_score: pred?.signal_score ?? game.signal_score,
        stability_classification: pred?.stability_classification || game.stability_classification,
      }
    : game;
  const start = formatKickoff(game?.start || game?.commence_time);
  const sportSlug = getSportSlugForLeague(game?.league || game?.sport_key || 'mlb');
  const blob = JSON.stringify(rankings || []).toLowerCase();
  const inOfficialParlay = Boolean(
    game?.home_team &&
      game?.away_team &&
      blob.includes(game.home_team.toLowerCase()) &&
      blob.includes(game.away_team.toLowerCase())
  );

  return (
    <main className="inqsi-shell tool-shell game-sheet">
      <AppHeader title={game?.matchup || 'Game'} apiStatus={apiStatus} apiDetail={apiDetail} />
      <nav className="tool-tabs" aria-label="InQsi tools">
        <Link className="tool-tab" href="/">Games</Link>
        <Link className="tool-tab" href="/arbitrage-v2">ARB</Link>
        <Link className="tool-tab" href="/parlays">3-Leg</Link>
        <Link className="tool-tab" href="/game-leans">Leans</Link>
        <Link className="tool-tab" href="/parlay-scanner">Scan</Link>
      </nav>
      <section className="game-sheet-head">
        <small>{game?.league || game?.sport_key || 'SPORT'} · {start || 'Waiting'}</small>
        <h2>{game?.matchup || 'Waiting on this matchup'}</h2>
        <p className="movement">Tap Lean, ARB, 3-Leg, or Scan for this game. InQsi does not invent a winner when no prediction is published.</p>
      </section>
      <GameTools game={merged} sportSlug={sportSlug} inOfficialParlay={inOfficialParlay} />
    </main>
  );
}
