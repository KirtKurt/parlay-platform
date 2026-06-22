import type { Metadata } from 'next';
import { AppHeader } from '@/components/AppHeader';
import { SlipScannerClient } from '@/components/SlipScannerClient';

export const metadata: Metadata = {
  title: 'AI Slip Scanner',
  description: 'Enter your own slip and let InQsi review line movement, weak-leg risk, signals, and market stability before you lock it in.',
  alternates: { canonical: '/parlay-scanner' }
};

const reviewChecks = [
  'Market movement path',
  'Steam, resistance, reversal, and chaos flags',
  'Weak-leg exposure',
  'Favorite and underdog pressure',
  'Line shopping and best available number',
  'Do-not-force warning when the slip looks unstable'
];

export default function Page() {
  return (
    <main className="inqsi-shell">
      <AppHeader eyebrow="InQsi" title="AI Slip Scanner" />

      <section className="inqsi-hero inqsi-seo-hero">
        <div className="inqsi-hero-card">
          <p className="inqsi-promo">AI Slip Scanner</p>
          <h1>Paste your slip. Find where your picks go wrong.</h1>
          <p>
            Bring the picks you already like. InQsi checks the slip against market movement, signal strength, weak-leg risk, and stability before you lock anything in.
          </p>
          <div className="inqsi-stat-grid" aria-label="Scanner flow">
            <div><b>1</b><span>Enter your picks</span></div>
            <div><b>2</b><span>Match to the market</span></div>
            <div><b>3</b><span>Scan the risk</span></div>
            <div><b>4</b><span>See the warning signs</span></div>
          </div>
        </div>

        <aside className="inqsi-signup-card">
          <h2>AI Slip Scanner</h2>
          <p>Start with the picks you already have. The scanner is built to slow you down, show weak spots, and help you decide whether the slip still deserves your trust.</p>
          <a href="/register?source=ai-slip-scanner">Start 5 days free</a>
          <a href="/sports">View sports board</a>
          <small>No fake grades. If verified market data is unavailable, InQsi shows a clear waiting state.</small>
        </aside>
      </section>

      <SlipScannerClient />

      <section className="inqsi-layout" style={{ marginTop: 20 }}>
        <div className="inqsi-panel">
          <div className="inqsi-section-head"><h2>Your slip</h2><span>3-leg scan</span></div>
          <div className="inqsi-game-list">
            {[1, 2, 3].map((leg) => (
              <article className="inqsi-game-card" key={leg}>
                <div className="inqsi-game-row"><b>Leg {leg}</b><span className="inqsi-score-chip">Ready</span></div>
                <div className="inqsi-market-grid">
                  <div><span>Sport</span>Select sport</div>
                  <div><span>Pick type</span>ML / Spread / Total</div>
                  <div><span>Your line</span>Enter odds</div>
                </div>
                <p className="inqsi-empty" style={{ marginTop: 12 }}>Add the team, player, market, line, or odds you want scanned.</p>
              </article>
            ))}
          </div>
        </div>

        <aside className="inqsi-panel">
          <div className="inqsi-section-head"><h2>What the AI scans</h2><span>Risk review</span></div>
          <div className="inqsi-game-list">
            {reviewChecks.map((check) => (
              <article className="inqsi-mini-card" key={check}>
                <b>{check}</b>
                <small>Runs when your pick is matched to verified market data.</small>
              </article>
            ))}
          </div>
        </aside>
      </section>

      <section className="inqsi-panel">
        <div className="inqsi-section-head"><h2>Scan result preview</h2><span>Risk check</span></div>
        <div className="inqsi-feature-grid">
          <article><b>Overall read</b><span>Clear / caution / do not force</span></article>
          <article><b>Strongest leg</b><span>The cleanest market support in the slip</span></article>
          <article><b>Weakest leg</b><span>The leg most likely to break structure</span></article>
          <article><b>Best available line</b><span>Line-shopping note when market data supports it</span></article>
        </div>
      </section>
    </main>
  );
}
