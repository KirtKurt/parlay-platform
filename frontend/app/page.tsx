import type { Metadata } from 'next';
import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';

const SPORTS = ['NFL', 'NCAAF', 'NBA', 'NCAAM', 'NHL', 'MLB', 'WNBA', 'Soccer', 'Tennis', 'MMA', 'Boxing', 'Golf', 'eSports'];
const CORE_LINKS = [
  ['AI Bet Slip Scanner', '/parlay-scanner'],
  ['Predicted winners', '/predicted-winners'],
  ['Best lines', '/best-lines'],
  ['Live market mode', '/live-market'],
  ['Public performance', '/performance'],
  ['Alerts', '/alerts'],
  ['Closing line value', '/clv'],
  ['Watchlist', '/watchlist'],
  ['Admin readiness', '/admin'],
  ['Launch checklist', '/launch-checklist'],
  ['Methodology', '/methodology'],
  ['Pricing', '/pricing'],
  ['Safe use', '/legal/safe-use'],
  ['Privacy choices', '/privacy-choices'],
  ['Contact', '/contact']
];

export const metadata: Metadata = {
  title: 'InQsi | Sports Market Intelligence',
  description: 'InQsi evaluates sportsbook markets and thousands of data points to help scan bet slips, surface risk, and review market movement before you lock it in.',
  alternates: { canonical: '/' }
};

function WorkingCard({ title, copy }: { title: string; copy: string }) {
  return <div className="inqsi-empty" role="status" aria-live="polite"><strong>{title}</strong><span>{copy}</span></div>;
}

function MockGameCard({ label }: { label: string }) {
  return (
    <article className="inqsi-game-card">
      <div className="inqsi-game-row"><b>{label}</b><span className="inqsi-score-chip">Working</span></div>
      <div className="inqsi-team-stack">
        <div className="inqsi-team"><span className="inqsi-jersey">A</span><span><b>Market side A</b><small>Verified feed required</small></span></div>
        <div className="inqsi-team"><span className="inqsi-jersey">B</span><span><b>Market side B</b><small>No artificial data shown</small></span></div>
      </div>
      <div className="inqsi-market-grid"><div><span>ML</span>Working</div><div><span>Spread</span>Working</div><div><span>Total</span>Working</div></div>
      <div className="inqsi-signal-row"><span>▲ Steam</span><span>▬ Resistance</span><span>⬡ Chaos</span><span>⟳ Review</span></div>
    </article>
  );
}

export default function Home() {
  return (
    <main className="inqsi-shell">
      <a className="inqsi-skip-link" href="#main-content">Skip to main content</a>
      <AppHeader eyebrow="InQsi" title="Market Intelligence" />

      <section className="inqsi-hero inqsi-mockup-hero" id="main-content">
        <div className="inqsi-hero-card">
          <p className="inqsi-promo">5 days free promo · Cancel anytime</p>
          <h1>Find what looks wrong <span>before you lock it in.</span></h1>
          <p>InQsi helps bettors find hidden risk before they lock in a slip. The platform evaluates sportsbook markets, analyzes thousands of market signals, and scans your bet slip for weak legs, instability, and warning signs.</p>
          <div className="inqsi-stat-grid" aria-label="InQsi value proposition">
            <div><b>Sportsbooks Evaluated</b><span>Major sportsbook markets monitored</span></div>
            <div><b>Market Signals</b><span>Thousands of data points analyzed</span></div>
            <div><b>Risk Detection</b><span>Weak legs surfaced before lock-in</span></div>
            <div><b>AI Bet Slip Scanner</b><span>Your slip scanned for what looks wrong</span></div>
          </div>
        </div>
        <aside className="inqsi-signup-card" aria-label="Create account"><h2>Start with 5 days free</h2><p>Save watchlists, alerts, scans, dashboards, and InQsi game leans.</p><a href="/parlay-scanner">Open AI Bet Slip Scanner</a><a href="/register">Continue with Google</a><a href="/register">Continue with Apple</a><a className="inqsi-primary" href="/register">Create account</a><small>Google and Apple sign-in are ready visually. Provider keys still need to be connected.</small></aside>
      </section>

      <nav className="inqsi-tabs" aria-label="Sports navigation">{SPORTS.map((sport) => <Link key={sport} href={`/sports?tab=${encodeURIComponent(sport)}`}>{sport}</Link>)}</nav>

      <section className="inqsi-layout inqsi-mock-dashboard">
        <div>
          <section className="inqsi-panel"><div className="inqsi-section-head"><h2>Sports Market Board</h2><span>Working on it</span></div><div className="inqsi-game-list"><MockGameCard label="Game slot 1" /><MockGameCard label="Game slot 2" /></div></section>
          <section className="inqsi-feature-grid" aria-label="Product features"><article><b>AI Bet Slip Scanner</b><span>Bring your own selections and scan for weak-leg risk.</span></article><article><b>Market Data</b><span>Review verified market sources for moneyline, spread, and totals when connected.</span></article><article><b>CLV Tracking</b><span>Store review point versus later market position.</span></article><article><b>Watchlist + Alerts</b><span>Steam, reversal, chaos, and final-check warnings.</span></article><article><b>Live Status Mode</b><span>Close-to-live status where supported.</span></article><article><b>Public Performance</b><span>Sport-specific accuracy and containment history.</span></article><article><b>Context Layer</b><span>Hooks for injuries, weather, starters, and news.</span></article><article><b>Admin Readiness</b><span>Provider, auth, billing, privacy, and launch checks.</span></article></section>
        </div>
        <aside className="inqsi-sidebar"><section className="inqsi-panel"><div className="inqsi-section-head"><h2>Game Leans</h2><span>Working on it</span></div><WorkingCard title="Game leans working on it" copy="Game leans appear when verified data is available." /></section><section className="inqsi-panel"><div className="inqsi-section-head"><h2>Bet Slip Review</h2><span>AI scanner</span></div><WorkingCard title="Scanner ready visually" copy="The scanner will evaluate a user-entered bet slip once verified market data is connected." /></section><section className="inqsi-panel"><div className="inqsi-section-head"><h2>Readiness</h2><span>Admin</span></div><WorkingCard title="Provider checks live" copy="Use /admin or /api/admin/summary to inspect readiness." /></section></aside>
      </section>

      <section className="inqsi-panel" id="performance"><div className="inqsi-section-head"><h2>Public Performance</h2><span>Sport siloed</span></div><WorkingCard title="Performance working on it" copy="Verified records will appear after enough final results are graded." /></section>
      <nav className="inqsi-seo-links" aria-label="InQsi pages">{CORE_LINKS.map(([label, href]) => <Link key={label} href={href}>{label}</Link>)}</nav>
      <section id="signup" className="inqsi-auth-modal" aria-label="Signup modal"><div><a className="inqsi-close" href="#" aria-label="Close signup modal">×</a><h2>Start 5 days free</h2><p>Sign up or log in to save your dashboard.</p><button>Continue with Google</button><button>Continue with Apple</button><label className="inqsi-visually-hidden" htmlFor="inqsi-email">Email address</label><input id="inqsi-email" placeholder="Email address" type="email" /><button className="inqsi-primary">Continue</button><small>Working on it: OAuth provider keys still need to be connected before live sign-in.</small></div></section>
    </main>
  );
}
