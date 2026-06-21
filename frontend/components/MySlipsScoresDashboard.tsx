'use client';

import { useMemo, useState } from 'react';
import {
  bestLineWarnings,
  buildAccuracyWindows,
  challengeScoringModel,
  MAX_PARLAY_LEGS,
  postGameAutopsy,
  publicProfileSnapshot,
  savedSlips,
  scoreSlip,
  SavedSlip,
  SlipVisibility,
  validateSlipLegCount
} from '@/lib/inqsi-slip-scoring';

function VisibilityBadge({ visibility }: { visibility: SlipVisibility }) {
  return <span className="league-chip">{visibility === 'public' ? 'Public' : 'Private'}</span>;
}

function SlipCard({ slip, onVisibilityChange }: { slip: SavedSlip; onVisibilityChange: (id: string, visibility: SlipVisibility) => void }) {
  const score = scoreSlip(slip);
  const validation = validateSlipLegCount(slip.legs);
  const warnings = bestLineWarnings(slip);
  const autopsy = postGameAutopsy(slip);

  return (
    <article className="panel" style={{ marginBottom: 20 }}>
      <div className="panel-header compact">
        <div>
          <p className="eyebrow blue">Saved slip</p>
          <h3>{slip.title}</h3>
        </div>
        <VisibilityBadge visibility={slip.visibility} />
      </div>

      <div className="status-row" style={{ marginBottom: 18 }}>
        <article className="status-card"><span>Legs</span><strong>{slip.legs.length}/{MAX_PARLAY_LEGS}</strong><p>{validation.message}</p></article>
        <article className="status-card"><span>Accuracy</span><strong>{score.accuracy}%</strong><p>{score.headline}</p></article>
        <article className="status-card"><span>Parlay</span><strong>{score.parlayHit ? 'Hit' : slip.status === 'pending' ? 'Pending' : 'Missed'}</strong><p>{slip.finalNote}</p></article>
      </div>

      <div className="hero-actions" style={{ marginBottom: 18 }}>
        <button className={slip.visibility === 'private' ? 'primary-button large' : 'ghost-button large'} type="button" onClick={() => onVisibilityChange(slip.id, 'private')}>Keep Private</button>
        <button className={slip.visibility === 'public' ? 'primary-button large' : 'ghost-button large'} type="button" onClick={() => onVisibilityChange(slip.id, 'public')}>Display Publicly</button>
      </div>

      <div className="game-list">
        {slip.legs.map((leg) => (
          <article className="game-card" key={leg.id}>
            <div className="game-topline"><span className="league-chip">{leg.market}</span><span>{leg.result}</span></div>
            <h4>{leg.selection}</h4>
            <p className="movement">{leg.game} · picked {leg.pickedLine} · best shown {leg.bestLine}</p>
          </article>
        ))}
      </div>

      <section className="content-grid" style={{ marginTop: 18 }}>
        <article className="panel">
          <div className="panel-header compact"><div><p className="eyebrow blue">Best-line warning layer</p><h3>You are leaving value on the table</h3></div></div>
          <div className="game-list">
            {warnings.map((warning) => (
              <article className="game-card" key={warning.legId}>
                <div className="game-topline"><span className="league-chip">{warning.severity}</span><span>{warning.selection}</span></div>
                <p className="movement">{warning.message}</p>
              </article>
            ))}
          </div>
        </article>

        <aside className="panel">
          <p className="eyebrow blue">Post-game autopsy</p>
          <h3>{autopsy.title}</h3>
          <p className="movement">{autopsy.summary}</p>
          {autopsy.failedLegs.length > 0 && (
            <div className="game-list" style={{ marginTop: 14 }}>
              {autopsy.failedLegs.map((leg) => (
                <article className="game-card" key={leg.selection}>
                  <h4>{leg.selection}</h4>
                  <p className="movement">{leg.warning}</p>
                </article>
              ))}
            </div>
          )}
        </aside>
      </section>
    </article>
  );
}

export function MySlipsScoresDashboard() {
  const [slips, setSlips] = useState(savedSlips);
  const accuracyWindows = useMemo(() => buildAccuracyWindows(slips), [slips]);
  const publicCount = slips.filter((slip) => slip.visibility === 'public').length;

  function setVisibility(id: string, visibility: SlipVisibility) {
    setSlips((current) => current.map((slip) => slip.id === id ? { ...slip, visibility } : slip));
  }

  return (
    <>
      <section className="status-row">
        <article className="status-card"><span>Visibility</span><strong>Private default</strong><p>Customers choose which slips can be public.</p></article>
        <article className="status-card"><span>Comments</span><strong>Off</strong><p>No customer comments on slips for now.</p></article>
        <article className="status-card"><span>Build cap</span><strong>{MAX_PARLAY_LEGS} legs</strong><p>No 4-leg or larger parlay builds.</p></article>
        <article className="status-card"><span>Public slips</span><strong>{publicCount}</strong><p>Shown only when the owner opts in.</p></article>
      </section>

      <section className="panel" style={{ marginTop: 20 }}>
        <div className="panel-header compact"><div><p className="eyebrow blue">Accuracy dashboard</p><h3>Combined score over time</h3></div></div>
        <div className="feature-grid" style={{ marginTop: 16 }}>
          {accuracyWindows.map((window) => (
            <article key={window.label}>
              <b>{window.accuracy}%</b>
              <span>{window.label} · {window.record}</span>
            </article>
          ))}
        </div>
      </section>

      <section className="content-grid" style={{ marginTop: 20 }}>
        <article className="panel">
          <div className="panel-header compact"><div><p className="eyebrow blue">Public profile card</p><h3>{publicProfileSnapshot.displayName}</h3></div></div>
          <p className="movement">@{publicProfileSnapshot.handle}</p>
          <p className="movement">{publicProfileSnapshot.headline}</p>
          <div className="status-row" style={{ marginTop: 14 }}>
            <article className="status-card"><span>Public slips</span><strong>{publicCount}</strong><p>Owner controlled.</p></article>
            <article className="status-card"><span>Comments</span><strong>Off</strong><p>No comments enabled.</p></article>
          </div>
        </article>

        <aside className="panel">
          <p className="eyebrow blue">Challenge-ready scoring model</p>
          <h3>Build the foundation now. Turn on challenges later.</h3>
          <p className="movement">Status: {challengeScoringModel.status}</p>
          <div className="game-list" style={{ marginTop: 14 }}>
            {challengeScoringModel.rankingInputs.map((input) => <article className="game-card" key={input}><p className="movement">{input}</p></article>)}
          </div>
        </aside>
      </section>

      <section style={{ marginTop: 20 }}>
        {slips.map((slip) => <SlipCard key={slip.id} slip={slip} onVisibilityChange={setVisibility} />)}
      </section>
    </>
  );
}
