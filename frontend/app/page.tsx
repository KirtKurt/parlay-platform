import type { Metadata } from 'next';
import Link from 'next/link';
import { getInqsiSnapshot, supportedSports, type InqsiGame } from '@/lib/api';

export const metadata: Metadata = {
  title: 'InQsi | Sports Market Intelligence, Predicted Winners & Parlay Scanner',
  description:
    'InQsi checks sportsbook line movement, predicted winners, live odds, parlay rankings, alerts, best available lines, and market risk before you lock it in.',
  alternates: { canonical: '/' },
  openGraph: {
    title: 'InQsi | Find What Looks Wrong Before You Lock It In',
    description:
      'A mobile-first sports market intelligence platform for signals, live odds, predicted winners, parlay scanning, watchlists, alerts, and best available lines.'
  },
  twitter: {
    card: 'summary_large_image',
    title: 'InQsi | Sports Market Intelligence',
    description: 'Find what looks wrong before you lock it in.'
  }
};

function teamInitials(name?: string) {
  return (name || 'TM')
    .split(/\s+/)
    .filter(Boolean)
    .slice(-2)
    .map((word) => word[0])
    .join('')
    .toUpperCase();
}

function homeTeam(game: InqsiGame) {
  return game.home_team || game.homeTeam || 'Home team working on it';
}

function awayTeam(game: InqsiGame) {
  return game.away_team || game.awayTeam || 'Away team working on it';
}

function startTime(game: InqsiGame) {
  const value = game.commence_time || game.commenceTime;
  if (!value) return 'Start time working on it';
  try {
    return new Date(value).toLocaleString();
  } catch {
    return 'Start time working on it';
  }
}

function WorkingCard({ title, copy }: { title: string; copy: string }) {
  return (
    <div className="inqsi-empty">
      <strong>{title}</strong>
      <span>{copy}</span>
    </div>
  );
}

function GameCard({ game }: { game: InqsiGame }) {
  const home = homeTeam(game);
  const away = awayTeam(game);
  const status = game.market_status || {};
  return (
    <article className="inqsi-game-card">
      <div className="inqsi-game-row">
        <div className="inqsi-team-stack">
          <div className="inqsi-team">
            <span className="inqsi-jersey">{teamInitials(away)}</span>
            <span><b>{away}</b><small>Away</small></span>
          </div>
          <div className="inqsi-team">
            <span className="inqsi-jersey">{teamInitials(home)}</span>
            <span><b>{home}</b><small>Home · {startTime(game)}</small></span>
          </div>
        </div>
        <div className="inqsi-score-chip">{game.signal_score || status.confidenceHigh || '—'}</div>
      </div>
      <div className="inqsi-market-grid">
        <div><span>Moneyline</span><b>{game.moneyline || 'Working on it'}</b></div>
        <div><span>Spread</span><b>{game.spread || 'Working on it'}</b></div>
        <div><span>O/U</span><b>{game.total || 'Working on it'}</b></div>
      </div>
      <div className="inqsi-signal-row">
        <span>{game.primary_signal || status.primarySignal || 'Signal working on it'}</span>
        <span>{game.stability_classification || status.riskLevel || 'Waiting on data'}</span>
        <span>{game.what_looks_wrong || status.recommendation || 'Review before you lock it in'}</span>
      </div>
    </article>
  );
}

export default async function Home({ searchParams }: { searchParams?: { sport?: string } }) {
  const selectedSport = searchParams?.sport || 'NBA';
  const snapshot = await getInqsiSnapshot(selectedSport);
  const selected = supportedSports.find((sport) => sport.key === snapshot.selectedSport) || supportedSports[2];
  const games = snapshot.games || [];
  const predictions = snapshot.predictions || [];
  const alerts = snapshot.alerts || [];

  return (
    <main className="inqsi-shell">
      <header className="inqsi-topbar">
        <Link className="inqsi-brand" href="/" aria-label="InQsi home">
          <span className="inqsi-logo-mark">Q</span>
          <span><b>InQsi</b><small>Market Intelligence</small></span>
        </Link>
        <nav className="inqsi-nav-actions">
          <a href="#performance">Performance</a>
          <a href="#signup">Log in</a>
          <a className="inqsi-primary" href="#signup">Start 5 days free</a>
        </nav>
      </header>

      <section className="inqsi-hero">
        <div className="inqsi-hero-card">
          <p className="inqsi-promo">5 days free promo · Cancel anytime</p>
          <h1>Find what looks wrong <span>before you lock it in.</span></h1>
          <p>
            InQsi tracks sportsbook movement, predicted winners, live odds, best available lines, parlay rankings, alerts, and market risk. When a feed is not ready, we say Working on it.
          </p>
          <div className="inqsi-stat-grid">
            <div><b>15 min</b><span>market snapshots</span></div>
            <div><b>3 min</b><span>close-to-live mode</span></div>
            <div><b>8 combos</b><span>3-leg parlay outcomes</span></div>
            <div><b>1 hour</b><span>winner lean visibility</span></div>
          </div>
        </div>
        <aside className="inqsi-signup-card">
          <h2>Start with 5 days free</h2>
          <p>Save watchlists, alerts, bet slip scans, dashboards, and InQsi winner leans.</p>
          <a href="#signup">Continue with Google</a>
          <a href="#signup">Continue with Apple</a>
          <a className="inqsi-primary" href="#signup">Create account</a>
          <small>Google and Apple sign-in are ready visually. Real OAuth keys still need to be connected.</small>
        </aside>
      </section>

      <section className="inqsi-tabs" aria-label="Sports">
        {supportedSports.map((sport) => (
          <Link key={sport.key} className={sport.key === selected.key ? 'active' : ''} href={`/?sport=${sport.key}`}>{sport.label}</Link>
        ))}
      </section>

      <section className="inqsi-layout">
        <div>
          <section className="inqsi-panel">
            <div className="inqsi-section-head">
              <h2>{selected.label} Market Board</h2>
              <span>{snapshot.apiStatus === 'CONNECTED' ? 'API connected' : 'Working on it'}</span>
            </div>
            {games.length ? (
              <div className="inqsi-game-list">{games.map((game, index) => <GameCard key={game.game_id || game.id || index} game={game} />)}</div>
            ) : (
              <WorkingCard title="Working on it" copy="The market feed for this sport is warming up. No fake teams, no fake lines." />
            )}
          </section>

          <section className="inqsi-feature-grid">
            <article><b>Bet Slip Scanner</b><span>Check three games and rank all 8 possible outcomes.</span></article>
            <article><b>Best Available Line</b><span>Compare books for moneyline, spread, and totals.</span></article>
            <article><b>CLV Tracking</b><span>Store prediction line versus closing line.</span></article>
            <article><b>Watchlist + Alerts</b><span>Steam, reversal, chaos, and final-check warnings.</span></article>
            <article><b>Live Market Mode</b><span>Live score and close-to-live odds where supported.</span></article>
            <article><b>Public Performance</b><span>Sport-specific accuracy and parlay containment.</span></article>
            <article><b>Context Layer</b><span>Hooks for injuries, weather, starters, and news.</span></article>
            <article><b>Community</b><span>Leaderboard foundation for verified slips.</span></article>
          </section>
        </div>

        <aside className="inqsi-sidebar">
          <section className="inqsi-panel">
            <div className="inqsi-section-head"><h2>Predicted Winners</h2><span>1 hour pre-start</span></div>
            {predictions.length ? predictions.map((pick, index) => (
              <div className="inqsi-mini-card" key={pick.game_id || index}>
                <b>InQsi leans {pick.predicted_winner || pick.predicted_team || 'Working on it'}</b>
                <small>{pick.short_explanation || 'Market explanation working on it.'}</small>
              </div>
            )) : <WorkingCard title="Winner leans working on it" copy="Predicted winners appear 1 hour before start when verified data is available." />}
          </section>

          <section className="inqsi-panel">
            <div className="inqsi-section-head"><h2>Auto-Parlay</h2><span>2 anchors + 1 risk</span></div>
            {snapshot.autoParlay?.built ? <pre className="inqsi-json">{JSON.stringify(snapshot.autoParlay, null, 2)}</pre> : <WorkingCard title="Auto-parlay working on it" copy="We will not force a parlay without verified market data." />}
          </section>

          <section className="inqsi-panel">
            <div className="inqsi-section-head"><h2>Alerts</h2><span>Market changes</span></div>
            {alerts.length ? alerts.map((alert, index) => <div className="inqsi-mini-card" key={index}><b>{alert.type || 'Alert'}</b><small>{alert.message || 'Market alert'}</small></div>) : <WorkingCard title="No alerts yet" copy="Quiet market. We are watching for steam, reversal, and chaos." />}
          </section>
        </aside>
      </section>

      <section className="inqsi-panel" id="performance">
        <div className="inqsi-section-head"><h2>Public Performance</h2><span>Sport siloed</span></div>
        <WorkingCard title="Performance working on it" copy="Autopsy records will appear after enough final results are graded." />
      </section>

      <section className="inqsi-seo-links" aria-label="Media and feature links">
        {['Predicted winners','Best sportsbook lines','Parlay scanner','Live market mode','Public performance','Sports market alerts','Closing line value','Watchlist','Injury and news context','X / Twitter','Instagram','TikTok','YouTube','Discord','LinkedIn'].map((label) => <a key={label} href="#signup">{label}</a>)}
      </section>

      <section id="signup" className="inqsi-auth-modal">
        <div>
          <a className="inqsi-close" href="#">×</a>
          <h2>Start 5 days free</h2>
          <p>Sign up or log in to save your InQsi dashboard.</p>
          <button>Continue with Google</button>
          <button>Continue with Apple</button>
          <input placeholder="Email address" type="email" />
          <button className="inqsi-primary">Continue</button>
          <small>Working on it: OAuth provider keys still need to be connected before live sign-in.</small>
        </div>
      </section>
    </main>
  );
}
