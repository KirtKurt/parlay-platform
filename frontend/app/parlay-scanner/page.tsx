import type { Metadata } from 'next';
import { AppHeader } from '@/components/AppHeader';

export const metadata: Metadata = {
  title: 'Evaluate My Bet Slip',
  description: 'Enter your own 3-leg parlay and let InQsi review line movement, weak-leg risk, signals, and market stability.',
  alternates: { canonical: '/parlay-scanner' }
};

const reviewChecks = [
  '15-minute market movement path',
  'Steam, resistance, reversal, and chaos flags',
  'Weak-leg exposure',
  'Favorite/underdog pressure',
  'Line shopping and best available number',
  'Do-not-force warning when the structure is unstable'
];

export default function Page() {
  return (
    <main className="inqsi-shell">
      <AppHeader eyebrow="InQsi" title="Evaluate my bet slip" />

      <section className="inqsi-hero inqsi-seo-hero">
        <div className="inqsi-hero-card">
          <p className="inqsi-promo">Bet slip evaluator</p>
          <h1>Paste your parlay. InQsi shows what looks wrong.</h1>
          <p>
            This is where a member brings their own 3-leg parlay for review. InQsi checks the selections against market movement,
            signal strength, weak-leg risk, and stability before the user locks anything in.
          </p>
          <div className="inqsi-stat-grid" aria-label="Evaluation flow">
            <div><b>1</b><span>Enter selections</span></div>
            <div><b>2</b><span>Match to live market</span></div>
            <div><b>3</b><span>Run risk review</span></div>
            <div><b>4</b><span>Return pass/caution</span></div>
          </div>
        </div>

        <aside className="inqsi-signup-card">
          <h2>Evaluate My Bet Slip</h2>
          <p>Full evaluation unlocks for members. The public preview shows the workflow without inventing live results.</p>
          <a href="/register?source=bet-slip-evaluator">Start 5 days free</a>
          <a href="/sports">View sports board</a>
          <small>No fake grades. If sportsbook data is not connected yet, InQsi shows Working on it.</small>
        </aside>
      </section>

      <section className="inqsi-layout">
        <div className="inqsi-panel">
          <div className="inqsi-section-head"><h2>Your bet slip</h2><span>3-leg review</span></div>
          <div className="inqsi-game-list">
            {[1, 2, 3].map((leg) => (
              <article className="inqsi-game-card" key={leg}>
                <div className="inqsi-game-row"><b>Leg {leg}</b><span className="inqsi-score-chip">Waiting</span></div>
                <div className="inqsi-market-grid">
                  <div><span>Sport</span>Select sport</div>
                  <div><span>Pick type</span>ML / Spread / Total</div>
                  <div><span>Your line</span>Enter odds</div>
                </div>
                <p className="inqsi-empty" style={{ marginTop: 12 }}>Input field placeholder: Team / player / market / line / odds</p>
              </article>
            ))}
          </div>
        </div>

        <aside className="inqsi-panel">
          <div className="inqsi-section-head"><h2>What InQsi evaluates</h2><span>Risk review</span></div>
          <div className="inqsi-game-list">
            {reviewChecks.map((check) => (
              <article className="inqsi-mini-card" key={check}>
                <b>{check}</b>
                <small>Runs when the selection is matched to verified market data.</small>
              </article>
            ))}
          </div>
        </aside>
      </section>

      <section className="inqsi-panel">
        <div className="inqsi-section-head"><h2>Evaluation result preview</h2><span>Working on it</span></div>
        <div className="inqsi-feature-grid">
          <article><b>Overall read</b><span>Pass / caution / do not force</span></article>
          <article><b>Strongest leg</b><span>The cleanest market support in the slip</span></article>
          <article><b>Weakest leg</b><span>The leg most likely to break structure</span></article>
          <article><b>Best available line</b><span>Line-shopping note when provider data supports it</span></article>
        </div>
      </section>
    </main>
  );
}
