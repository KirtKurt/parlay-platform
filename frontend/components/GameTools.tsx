'use client';

import { useState } from 'react';
import Link from 'next/link';

type GameLike = {
  id?: string;
  game_id?: string;
  league?: string;
  sport_key?: string;
  matchup?: string;
  home_team?: string;
  away_team?: string;
  favorite?: string;
  predicted_winner?: string;
  predicted_side?: string;
  confidence?: string | number;
  marketNote?: string;
  short_explanation?: string;
  primary_signal?: string;
  signal_score?: number;
  stability_classification?: string;
  risk?: string;
  movement?: string;
};

type Tab = 'lean' | 'arb' | 'parlay' | 'scan';

export function GameTools({
  game,
  sportSlug,
  inOfficialParlay
}: {
  game?: GameLike | null;
  sportSlug: string;
  inOfficialParlay?: boolean;
}) {
  const [tab, setTab] = useState<Tab>('lean');
  const lean = game?.predicted_winner || game?.predicted_side;
  const why = game?.short_explanation || game?.marketNote;

  return (
    <section className="game-sheet-panel">
      <div className="game-sheet-tabs" role="tablist" aria-label="Tools for this game">
        {([
          ['lean', 'Lean'],
          ['arb', 'ARB'],
          ['parlay', '3-Leg'],
          ['scan', 'Scan']
        ] as Array<[Tab, string]>).map(([id, label]) => (
          <button key={id} type="button" className={tab === id ? 'on' : ''} onClick={() => setTab(id)} aria-selected={tab === id}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'lean' && (
        <div>
          <h3>{lean || 'No published lean'}</h3>
          <p className="movement">
            {lean
              ? `${why || 'Published from the winner-prediction engine.'} Signal ${game?.primary_signal || 'Waiting'}${game?.signal_score != null ? ` · score ${game.signal_score}` : ''} · ${game?.stability_classification || game?.risk || 'stability waiting'}${game?.confidence != null ? ` · ${game.confidence}` : ''}.`
              : 'InQsi is not inventing a winner on this game. A lean appears when predicted_winner and an explanation are published.'}
          </p>
          {game?.movement && <p className="movement">{game.movement}</p>}
        </div>
      )}

      {tab === 'arb' && (
        <div>
          <h3>ARB on this game</h3>
          <p className="movement">Two-book stake math lives in the ARB command center. InQsi does not invent a surebet on this matchup from a single board quote.</p>
        </div>
      )}

      {tab === 'parlay' && (
        <div>
          <h3>{inOfficialParlay ? 'In the official 3-leg' : 'Not in the current official 3-leg'}</h3>
          <p className="movement">
            {inOfficialParlay
              ? 'This game is part of a published hourly structure. Open the official slip for the per-leg breakdown.'
              : 'Official 3-leg slips are published hourly. InQsi will not force this game into a three-leg just to fill a card.'}
          </p>
        </div>
      )}

      {tab === 'scan' && (
        <div>
          <h3>Scan a slip with this game</h3>
          <p className="movement">Bring your own 3-leg and check weak-leg risk against the live board. The scanner does not place the bet.</p>
        </div>
      )}

      <div className="game-sheet-actions">
        {tab === 'arb' && <Link href="/arbitrage-v2">Open ARB</Link>}
        {tab === 'parlay' && <Link href="/parlays">Open official 3-leg</Link>}
        {tab === 'scan' && <Link href="/parlay-scanner">Open scanner</Link>}
        <Link href={`/sports/${sportSlug}`}>{game?.league || 'Sport'} board</Link>
        <Link href="/game-leans">All leans</Link>
      </div>
    </section>
  );
}
