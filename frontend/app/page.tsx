import type { Metadata } from 'next';
import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';

const SPORTS = ['NFL', 'CFB', 'NBA', 'NCAAM', 'NHL', 'MLB', 'WNBA', 'Soccer', 'Tennis', 'MMA', 'Boxing', 'Golf', 'eSports'];
const FOOTER_LINKS = [
  ['AI Slip Scanner', '/parlay-scanner'],
  ['AI Slip Builder', '/parlays'],
  ['Game Leans', '/game-leans'],
  ['Best Lines', '/best-lines'],
  ['Live Market', '/live-market'],
  ['Line Movement Review', '/line-movement-review'],
  ['Review History', '/performance'],
  ['Alerts', '/alerts'],
  ['Watchlist', '/watchlist'],
  ['Methodology', '/methodology'],
  ['Pricing', '/pricing'],
  ['Privacy', '/legal/privacy'],
  ['Safe Use', '/legal/safe-use'],
  ['Contact', '/contact']
];

export const metadata: Metadata = {
  title: 'InQsi | AI Slip Scanner & Sports Market Review',
  description: 'InQsi helps members scan slips, review market signals, surface hidden risk, compare line movement, and learn from post-game score history.',
  alternates: { canonical: '/' }
};

function MarketPreviewCard({ label }: { label: string }) {
  return (
    <article className="inqsi-game-card">
      <div className="inqsi-game-row"><b>{label}</b><span className="inqsi-score-chip">Pending</span></div>
      <div className="inqsi-team-stack">
        <div className="inqsi-team"><span className="inqsi-jersey">A</span><span><b>Market side A</b><small>Waiting for market data</small></span></div>
        <div className="inqsi-team"><span className="inqsi-jersey">B</span><span><b>Market side B</b><small>No artificial data shown</small></span></div>
      </div>
      <div className="inqsi-market-grid"><div><span>ML</span>Pending</div><div><span>Spread</span>Pending</div><div><span>Total</span>Pending</div></div>
      <div className="inqsi-signal-row"><span>Market signals</span><span>Risk review</span><span>AI scan</span></div>
    </article>
  );
}

export default function Home() {
  const boardSlots = ['Game slot 1', 'Game slot 2', 'Game slot 3'];

  return (
    <main className="inqsi-shell">
      <a className="inqsi-skip-link" href="#main-content">Skip to main content</a>
      <AppHeader eyebrow="InQsi" title="Market Intelligence" />

      <section className="inqsi-hero inqsi-mockup-hero" id="main-content">
        <div className="inqsi-hero-card">
          <p className="inqsi-promo">5 days free promo · Cancel anytime</p>
          <h1>Find where your picks go wrong <span>before you lock it in.</span></h1>
          <p>InQsi helps members find hidden risk before they lock in a slip. The platform reviews market movement, scans thousands of market signals, and checks each slip for weak legs, instability, and warning signs.</p>
          <div className="inqsi-stat-grid" aria-label="InQsi value proposition">
            <div><b>Sportsbooks Evaluated</b><span>Major sportsbook markets monitored</span></div>
            <div><b>Market Signals</b><span>Thousands of data points analyzed</span></div>
            <div><b>Risk Detection</b><span>Weak legs surfaced before lock-in</span></div>
            <div><b>AI Slip Scanner</b><span>Your picks scanned for where they go wrong</span></div>
          </div>
        </div>
        <aside className="inqsi-signup-card" aria-label="Create account">
          <h2>Start with 5 days free</h2>
          <p>Scan your slip, save watchlists, review alerts, and track market intelligence in one member dashboard.</p>
          <a href="/parlay-scanner">Open AI Slip Scanner</a>
          <a className="inqsi-primary" href="/register">Create account</a>
          <small>Start with the scanner, review the board, and decide whether InQsi earns a place in your routine.</small>
        </aside>
      </section>

      <nav className="inqsi-tabs" aria-label="Sports navigation">{SPORTS.map((sport) => <Link key={sport} href={`/sports?tab=${encodeURIComponent(sport)}`}>{sport}</Link>)}</nav>

      <section className="inqsi-layout inqsi-mock-dashboard">
        <div>
          <section className="inqsi-panel">
            <div className="inqsi-section-head"><h2>Sports Market Board</h2><span>Market data pending</span></div>
            <div className="inqsi-game-list">{boardSlots.map((slot) => <MarketPreviewCard key={slot} label={slot} />)}</div>
          </section>
        </div>
        <aside className="inqsi-panel">
          <div className="inqsi-section-head"><h2>AI Slip Scanner</h2><span>Bring your picks</span></div>
          <p className="movement">Enter your own slip and let InQsi scan for weak legs, market instability, and warning signs.</p>
          <Link className="inqsi-primary" href="/parlay-scanner" style={{ textDecoration: 'none', width: '100%' }}>Scan my slip</Link>
        </aside>
      </section>

      <footer className="inqsi-footer-links" aria-label="InQsi footer navigation">
        {FOOTER_LINKS.map(([label, href]) => <Link key={label} href={href}>{label}</Link>)}
        <span aria-label="deployment version">deploy: member-fix-2026-06-21-01</span>
      </footer>
    </main>
  );
}
