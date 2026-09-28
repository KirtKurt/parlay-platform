import type { Metadata } from 'next';
import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { getApiSnapshot } from '@/lib/api';
import { formatKickoff, gamePath } from '@/lib/kickoff';

export const dynamic = 'force-dynamic';

export const metadata: Metadata = {
  title: 'Game Leans',
  description: 'Per-game InQsi predicted winners with signal score, stability, confidence, and a short explanation — not just line movement.',
  alternates: { canonical: '/game-leans' }
};

export default async function Page() {
  const { games, predictions, apiStatus, apiDetail } = await getApiSnapshot();
  const rows = games.map((game) => {
    const pred = predictions.find((row) =>
      row.game_id === game.id ||
      row.game_id === game.game_id ||
      (row.home_team === game.home_team && row.away_team === game.away_team)
    );
    return {
      game,
      lean: pred?.predicted_winner || pred?.predicted_side || game.predicted_winner || game.predicted_side,
      confidence: pred?.confidence_score ?? game.confidence,
      explanation: pred?.short_explanation || game.marketNote,
      signal: game.primary_signal,
      score: game.signal_score,
      stability: game.stability_classification || game.risk,
    };
  }).filter((row) => row.lean);

  return (
    <main className="inqsi-shell tool-shell">
      <AppHeader title="Game Leans" apiStatus={apiStatus} apiDetail={apiDetail} />

      <nav className="tool-tabs" aria-label="InQsi tools">
        <Link className="tool-tab" href="/">Games</Link>
        <Link className="tool-tab" href="/arbitrage-v2">ARB</Link>
        <Link className="tool-tab" href="/parlays">3-Leg</Link>
        <Link className="tool-tab on" href="/game-leans">Leans</Link>
        <Link className="tool-tab" href="/parlay-scanner">Scan</Link>
      </nav>

      <section className="tool-feed">
        <div className="tool-feed-head">
          <div>
            <p className="eyebrow">Predicted winners</p>
            <h2>Each game uses more than movement</h2>
          </div>
          <span className="data-status">{rows.length ? `${rows.length} leans` : 'Waiting'}</span>
        </div>
        <p className="movement">Leans come from the winner-prediction engine: market direction, signal score, stability, and a short explanation. InQsi does not invent a winner when those fields are empty.</p>
        {rows.length ? rows.map((row) => (
          <Link className="tool-row lean-row" href={gamePath(row.game)} key={row.game.id}>
            <div>
              <small>{row.game.league || row.game.sport_key} · {formatKickoff(row.game.start || row.game.commence_time)}</small>
              <strong>{row.game.matchup}</strong>
              <p><b>{row.lean}</b> · {row.explanation || 'Published lean from the live prediction table.'}</p>
              <p className="lean-meta">Signal {row.signal || 'Waiting'}{row.score != null ? ` · score ${row.score}` : ''} · {row.stability || 'stability waiting'}</p>
            </div>
            <b className="tool-edge">{row.confidence ?? 'Waiting'}</b>
          </Link>
        )) : (
          <article className="tool-row">
            <div>
              <small>WAITING</small>
              <strong>No published prediction yet</strong>
              <p>When winner-predictions are visible, each row shows the team, confidence, signal, and explanation. The board favorite is not used as a fake pick.</p>
            </div>
          </article>
        )}
      </section>
    </main>
  );
}
