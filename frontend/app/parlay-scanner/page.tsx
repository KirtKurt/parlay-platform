import type { Metadata } from 'next';
import { AppHeader } from '@/components/AppHeader';
import { SlipScannerClient } from '@/components/SlipScannerClient';

export const metadata: Metadata = {
  title: 'Scan My Slip | InQsi',
  description: 'Upload or enter a slip and let InQsi review line movement, weak-leg risk, signals, and market stability before lock-in.',
  alternates: { canonical: '/parlay-scanner' }
};

const reviewChecks = ['Risk Review', 'Market Signals', 'AI Scan'];

export default function Page() {
  return (
    <main className="inqsi-shell">
      <AppHeader eyebrow="InQsi" title="Scan My Slip" />

      <section className="inqsi-panel" style={{ textAlign: 'center', marginBottom: 18 }}>
        <p className="eyebrow blue">Scan My Slip</p>
        <h2>Scan Your Bet Slip</h2>
        <p className="movement" style={{ maxWidth: 620, margin: '0 auto 18px' }}>Scan or enter a 3-leg slip to break down each leg, review risk, and compare the pick to live market context.</p>
        <div className="hero-actions" style={{ justifyContent: 'center' }}>
          <button className="primary-button large" type="button">Upload Screenshot</button>
          <button className="ghost-button large" type="button">Open Camera</button>
        </div>
      </section>

      <SlipScannerClient />

      <section className="inqsi-panel" style={{ marginTop: 18 }}>
        <div className="inqsi-section-head"><h2>Analyzed Slip</h2><span className="data-status">3 Legs Detected</span></div>
        <div className="game-list">
          {[
            ['BOS Celtics -6.5', 'Spread', '-110', '6:00 PM'],
            ['LA Dodgers -1.5', 'Run Line', '-120', '7:15 PM'],
            ['OKC Thunder -4.0', 'Spread', '-105', '8:30 PM']
          ].map(([team, type, odds, start]) => (
            <article className="game-card" key={team}>
              <div className="game-topline"><span className="league-chip">LEG</span><span>{start}</span></div>
              <h4>{team}</h4>
              <div className="market-row">
                <div><span>Market</span><strong>{type}</strong><b>{odds}</b></div>
                <div><span>Signal</span><strong>Market Match</strong><b>Ready</b></div>
                <div><span>Risk</span><strong>Review</strong><b>Pending</b></div>
                <div><span>Status</span><strong>AI Scan</strong><b>Ready</b></div>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="inqsi-layout" style={{ marginTop: 18 }}>
        <div className="inqsi-panel">
          <div className="inqsi-section-head"><h2>Scan Results</h2><span>AI Analysis</span></div>
          <div className="inqsi-stat-grid">
            {reviewChecks.map((check) => <div key={check}><b>{check}</b><span>Runs when your pick is matched to market-board data.</span></div>)}
          </div>
        </div>
        <aside className="inqsi-panel">
          <div className="inqsi-section-head"><h2>Parlay Summary</h2><span>Preview</span></div>
          <div className="market-row">
            <div><span>Odds</span><strong>Overall</strong><b>+342</b></div>
            <div><span>Confidence</span><strong>Score</strong><b>72</b></div>
            <div><span>Structure</span><strong>3 Legs</strong><b>Clean</b></div>
            <div><span>Result</span><strong>Read</strong><b>Pending</b></div>
          </div>
        </aside>
      </section>
    </main>
  );
}
