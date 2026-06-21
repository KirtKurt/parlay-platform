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

const publicCards = [
  { handle: 'inqsi-member', name: 'InQsi Member', week: 67, month: 63, isFollowed: true },
  { handle: 'buffalo-market', name: 'Buffalo Market Read', week: 71, month: 58, isFollowed: false },
  { handle: 'three-leg-only', name: 'Three Leg Only', week: 62, month: 66, isFollowed: false }
];

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
  const [showWeekScore, setShowWeekScore] = useState(true);
  const [showMonthScore, setShowMonthScore] = useState(true);
  const accuracyWindows = useMemo(() => buildAccuracyWindows(slips), [slips]);
  const publicCount = slips.filter((slip) => slip.visibility === 'public').length;
  const weekScore = accuracyWindows.find((window) => window.label === '1 week');
  const monthScore = accuracyWindows.find((window) => window.label === '1 month');
  const leaderboardCards = [...publicCards].sort((a, b) => b.week - a.week);

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
          <p className="movement">Shareable card link: inqsi.app/u/{publicProfileSnapshot.handle}</p>
          <p className="movement">The customer controls whether the public card shows 1-week and 1-month cumulative score percentages.</p>
          <div className="hero-actions" style={{ marginTop: 14 }}>
            <button className={showWeekScore ? 'primary-button large' : 'ghost-button large'} type="button" onClick={() => setShowWeekScore((current) => !current)}>Show 1 Week %</button>
            <button className={showMonthScore ? 'primary-button large' : 'ghost-button large'} type="button" onClick={() => setShowMonthScore((current) => !current)}>Show 1 Month %</button>
          </div>
          <div className="status-row" style={{ marginTop: 14 }}>
            <article className="status-card"><span>Public slips</span><strong>{publicCount}</strong><p>Owner controlled.</p></article>
            <article className="status-card"><span>Comments</span><strong>Off</strong><p>No comments enabled.</p></article>
            {showWeekScore && weekScore && <article className="status-card"><span>1 week score</span><strong>{weekScore.accuracy}%</strong><p>{weekScore.record}</p></article>}
            {showMonthScore && monthScore && <article className="status-card"><span>1 month score</span><strong>{monthScore.accuracy}%</strong><p>{monthScore.record}</p></article>}
          </div>
        </article>

        <aside className="panel">
          <p className="eyebrow blue">Followed card discovery</p>
          <h3>Find public cards by handle.</h3>
          <p className="movement">Use Followed Profiles, not Friends. Customers can find a public card by handle and decide whether to follow the score card. No comments, messages, or copy-slip action at launch.</p>
          <div className="game-list" style={{ marginTop: 14 }}>
            {publicCards.map((card) => (
              <article className="game-card" key={card.handle}>
                <div className="game-topline"><span className="league-chip">@{card.handle}</span><span>{card.isFollowed ? 'Followed' : 'Available'}</span></div>
                <h4>{card.name}</h4>
                <p className="movement">1 week {card.week}% · 1 month {card.month}% · inqsi.app/u/{card.handle}</p>
              </article>
            ))}
          </div>
        </aside>
      </section>

      <section className="panel" style={{ marginTop: 20 }}>
        <div className="panel-header compact"><div><p className="eyebrow blue">Leaderboard foundation</p><h3>Score-based, controlled, and no comments.</h3></div></div>
        <p className="movement">Leaderboard should rank only public cards and only score windows the owner has chosen to show. Private slips stay excluded.</p>
        <div className="game-list" style={{ marginTop: 14 }}>
          {leaderboardCards.map((card, index) => (
            <article className="game-card" key={card.handle}>
              <div className="game-topline"><span className="league-chip">#{index + 1}</span><span>@{card.handle}</span></div>
              <h4>{card.name}</h4>
              <p className="movement">1 week {card.week}% · 1 month {card.month}%</p>
            </article>
          ))}
        </div>
        <div className="compliance-box" style={{ marginTop: 14 }}>Future challenge pages remain hidden for now. The scoring model exists, but no challenge route is exposed.</div>
      </section>

      <section style={{ marginTop: 20 }}>
        {slips.map((slip) => <SlipCard key={slip.id} slip={slip} onVisibilityChange={setVisibility} />)}
      </section>
    </>
  );
}
