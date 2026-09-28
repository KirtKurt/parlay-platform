import type { Metadata } from 'next';
import { AppHeader } from '@/components/AppHeader';
import { SlipScannerClient } from '@/components/SlipScannerClient';

export const metadata: Metadata = {
  title: 'Build My Slip',
  description: 'Build a 3-leg slip from verified live markets, compare sportsbook prices, and analyze the completed slip.',
  alternates: { canonical: '/parlay-scanner' }
};

const reviewChecks = ['Risk Review', 'Market Signals', 'AI Scan'];

export default function Page() {
  return (
    <main className="inqsi-shell">
      <AppHeader eyebrow="InQsi" title="Scan My Slip" />

      <SlipScannerClient />

      <section className="inqsi-panel" style={{ marginTop: 18 }}>
        <div className="inqsi-section-head"><h2>Analyzed Slip</h2><span className="data-status">Waiting</span></div>
        <article className="game-card">
          <div className="game-topline"><span className="league-chip">SLIP</span><span>Waiting</span></div>
          <h4>No sample legs</h4>
          <p className="movement">Upload or enter a live slip to see kickoff, market favorite, and odds for each leg.</p>
        </article>
      </section>

      <section className="inqsi-layout" style={{ marginTop: 18 }}>
        <div className="inqsi-panel">
          <div className="inqsi-section-head"><h2>Scan Results</h2><span>AI Analysis</span></div>
          <div className="inqsi-stat-grid">
            {reviewChecks.map((check) => <div key={check}><b>{check}</b><span>Runs when your pick is matched to market-board data.</span></div>)}
          </div>
        </div>
        <aside className="inqsi-panel">
          <div className="inqsi-section-head"><h2>Parlay Summary</h2><span>Live scan</span></div>
          <div className="market-row">
            <div><span>Odds</span><strong>Overall</strong><b>Waiting</b></div>
            <div><span>Confidence</span><strong>Score</strong><b>Waiting</b></div>
            <div><span>Structure</span><strong>Legs</strong><b>Waiting</b></div>
            <div><span>Result</span><strong>Read</strong><b>Waiting</b></div>
          </div>
        </aside>
      </section>
    </main>
  );
}
