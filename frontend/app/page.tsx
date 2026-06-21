import type { Metadata } from 'next';
import Link from 'next/link';

const SPORTS = ['NFL', 'NCAAF', 'NBA', 'NCAAM', 'NHL', 'MLB', 'WNBA', 'Soccer', 'Tennis', 'MMA', 'Boxing', 'Golf', 'eSports'];
const CORE_LINKS = [
  ['Predicted winners', '/predicted-winners'],
  ['Best lines', '/best-lines'],
  ['Parlay scanner', '/parlay-scanner'],
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
  description: 'InQsi shows market movement, game leans, line checks, ranked review tools, alerts, and performance tracking before you lock it in.',
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
      <header className="inqsi-topbar">
        <Link className="inqsi-brand" href="/" aria-label="InQsi home"><span className="inqsi-logo-mark" aria-hidden="true">Q</span><span><b>InQsi</b><small>Market Intelligence</small></span></Link>
        <nav className="inqsi-nav-actions" aria-label="Primary navigation"><Link href="/performance">Performance</Link><Link href="/admin">Admin</Link><a href="#signup">Log in</a><a className="inqsi-primary" href="#signup">Start 5 days free</a></nav>
      </header>

      <section className="inqsi-hero inqsi-mockup-hero" id="main-content">
        <div className="inqsi-hero-card">
          <p className="inqsi-promo">5 days free promo · Cancel anytime</p>
          <h1>Find what looks wrong <span>before you lock it in.</span></h1>
          <p>InQsi tracks market movement, game leans, live status, best available data, ranked review tools, alerts, and risk signals. If a feed is not ready, the app says Working on it instead of showing made-up data.</p>
          <div className="inqsi-stat-grid" aria-label="Platform timing and features"><div><b>15 min</b><span>market snapshots</span></div><div><b>3 min</b><span>close-to-live mode</span></div><div><b>8 combos</b><span>3-selection outcome ranking</span></div><div><b>1 hour</b><span>game lean window</span></div></div>
        </div>
        <aside className="inqsi-signup-card" aria-label="Create account"><h2>Start with 5 days free</h2><p>Save watchlists, alerts, scans, dashboards, and InQsi game leans.</p><a href="/register">Continue with Google</a><a href="/register">Continue with Apple</a><a className="inqsi-primary" href="/register">Create account</a><small>Google and Apple sign-in are ready visually. Provider keys still need to be connected.</small></aside>
      </section>

      <nav className="inqsi-tabs" aria-label="Sports navigation">{SPORTS.map((sport) => <Link key={sport} href={`/sports?tab=${encodeURIComponent(sport)}`}>{sport}</Link>)}</nav>

      <section className="inqsi-layout inqsi-mock-dashboard">
        <div>
          <section className="inqsi-panel"><div className="inqsi-section-head"><h2>Sports Market Board</h2><span>Working on it</span></div><div className="inqsi-game-list"><MockGameCard label="Game slot 1" /><MockGameCard label="Game slot 2" /></div></section>
          <section className="inqsi-feature-grid" aria-label="Product features"><article><b>Scanner</b><span>Review three selections and rank all 8 possible outcomes.</span></article><article><b>Market Data</b><span>Compare verified sources for moneyline, spread, and totals when connected.</span></article><article><b>CLV Tracking</b><span>Store review point versus later market position.</span></article><article><b>Watchlist + Alerts</b><span>Steam, reversal, chaos, and final-check warnings.</span></article><article><b>Live Status Mode</b><span>Close-to-live status where supported.</span></article><article><b>Public Performance</b><span>Sport-specific accuracy and containment history.</span></article><article><b>Context Layer</b><span>Hooks for injuries, weather, starters, and news.</span></article><article><b>Admin Readiness</b><span>Provider, auth, billing, privacy, and launch checks.</span></article></section>
        </div>
        <aside className="inqsi-sidebar"><section className="inqsi-panel"><div className="inqsi-section-head"><h2>Game Leans</h2><span>1 hour pre-start</span></div><WorkingCard title="Game leans working on it" copy="Game leans appear when verified data is available." /></section><section className="inqsi-panel"><div className="inqsi-section-head"><h2>Auto Review</h2><span>2 anchors + 1 risk</span></div><WorkingCard title="Auto review working on it" copy="We will not force a structure without verified market data." /></section><section className="inqsi-panel"><div className="inqsi-section-head"><h2>Readiness</h2><span>Admin</span></div><WorkingCard title="Provider checks live" copy="Use /admin or /api/admin/summary to inspect readiness." /></section></aside>
      </section>

      <section className="inqsi-panel" id="performance"><div className="inqsi-section-head"><h2>Public Performance</h2><span>Sport siloed</span></div><WorkingCard title="Performance working on it" copy="Verified records will appear after enough final results are graded." /></section>
      <nav className="inqsi-seo-links" aria-label="InQsi pages">{CORE_LINKS.map(([label, href]) => <Link key={label} href={href}>{label}</Link>)}</nav>
      <section id="signup" className="inqsi-auth-modal" aria-label="Signup modal"><div><a className="inqsi-close" href="#" aria-label="Close signup modal">×</a><h2>Start 5 days free</h2><p>Sign up or log in to save your dashboard.</p><button>Continue with Google</button><button>Continue with Apple</button><label className="inqsi-visually-hidden" htmlFor="inqsi-email">Email address</label><input id="inqsi-email" placeholder="Email address" type="email" /><button className="inqsi-primary">Continue</button><small>Working on it: OAuth provider keys still need to be connected before live sign-in.</small></div></section>
    </main>
  );
}
