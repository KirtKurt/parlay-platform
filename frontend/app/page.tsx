import type { Metadata } from 'next';
import Link from 'next/link';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { GameCard } from '@/components/GameCard';
import { RankingCard } from '@/components/RankingCard';
import { LineMovementGraph } from '@/components/LineMovementGraph';
import { PaidPreviewGate } from '@/components/PaidPreviewGate';
import { SportEquipmentIcon, SportIconStrip, TeamJerseyBadge } from '@/components/SportVisuals';

export const metadata: Metadata = {
  title: 'Silvers Syndicate | Parlay Risk Checker & Sports Market Intelligence',
  description:
    'See where your picks start to break down. Check line movement, steam, resistance, market anomaly alerts, weak-leg risk, and parlay structure before you lock in a ticket.',
  alternates: { canonical: '/' },
  openGraph: {
    title: 'Silvers Syndicate | See Where Your Picks Start to Break Down',
    description:
      'A premium sports market intelligence board for line movement, steam, resistance, weak-leg risk, and parlay risk checks. First week free.'
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Silvers Syndicate | See Where Your Picks Start to Break Down',
    description: 'Check your picks against market movement before you lock in a parlay.'
  }
};

const featuredMatchups = [
  {
    league: 'NFL',
    sport: 'nfl',
    time: '8:20 PM',
    home: { abbr: 'BUF', name: 'Buffalo', tone: 'blue', number: '17' },
    away: { abbr: 'MIA', name: 'Miami', tone: 'teal', number: '10' },
    market: 'BUF -4.5 · O/U 49.5',
    signal: 'STEAM',
    risk: 'LOW RISK',
    riskTone: 'low'
  },
  {
    league: 'NFL',
    sport: 'nfl',
    time: '4:25 PM',
    home: { abbr: 'DAL', name: 'Dallas', tone: 'silver', number: '4' },
    away: { abbr: 'PHI', name: 'Philadelphia', tone: 'green', number: '11' },
    market: 'PHI -3 · O/U 46.5',
    signal: 'RESISTANCE',
    risk: 'MEDIUM RISK',
    riskTone: 'med'
  },
  {
    league: 'NBA',
    sport: 'nba',
    time: '7:30 PM',
    home: { abbr: 'BOS', name: 'Boston', tone: 'green', number: '0' },
    away: { abbr: 'LAL', name: 'Los Angeles', tone: 'gold', number: '23' },
    market: 'LAL -2.5 · O/U 218.5',
    signal: 'COIN FLIP',
    risk: 'MEDIUM RISK',
    riskTone: 'med'
  },
  {
    league: 'CFB',
    sport: 'cfb',
    time: '3:30 PM',
    home: { abbr: 'UGA', name: 'Georgia', tone: 'red', number: '1' },
    away: { abbr: 'ALA', name: 'Alabama', tone: 'crimson', number: '15' },
    market: 'ALA -6.5 · O/U 52.5',
    signal: 'MARKET ANOMALY',
    risk: 'HIGH RISK',
    riskTone: 'high'
  }
];

const checkCards = [
  { sport: 'nfl', title: 'Steam', copy: 'Sharp money pushing a number before the public catches up.' },
  { sport: 'nhl', title: 'Resistance', copy: 'Line pushback that tells you the obvious side may not be clean.' },
  { sport: 'nba', title: 'Coin Flip', copy: 'A split market where one leg should be treated with caution.' },
  { sport: 'darts', title: 'Market Anomaly', copy: 'Odd movement the board cannot explain with normal pressure.' },
  { sport: 'mlb', title: '15-Minute Pulls', copy: 'Late movement checks that catch reversals and sharp swings.' },
  { sport: 'lacrosse', title: 'No-Overlap Structure', copy: 'Cleaner builds with less repeated exposure across tickets.' }
];

const guideCards = [
  {
    sport: 'tennis',
    title: 'How line movement helps you read a game',
    copy: 'Opening lines, mid-day moves, and late pulls can reveal whether a side is gaining trust or quietly losing support.'
  },
  {
    sport: 'soccer',
    title: 'Why some parlays fall apart before kickoff',
    copy: 'A ticket can look clean and still carry one weak leg. We show where that pressure starts to show up.'
  },
  {
    sport: 'table-tennis',
    title: 'What steam and resistance actually tell you',
    copy: 'Separate meaningful market pressure from noise so you can stop chasing every popular side.'
  }
];

function MiniSparkline({ tone = 'blue' }: { tone?: string }) {
  return (
    <svg className={`mini-spark tone-${tone}`} viewBox="0 0 160 54" role="img" aria-label="Line movement chart preview">
      <path d="M2 38 C16 36 18 28 32 31 C47 36 49 21 63 24 C78 27 77 14 92 18 C106 22 111 35 124 31 C138 26 141 17 158 15" />
      <line x1="52" y1="6" x2="52" y2="50" />
      <line x1="104" y1="6" x2="104" y2="50" />
      <text x="45" y="10">T1</text>
      <text x="97" y="10">T2</text>
      <circle cx="158" cy="15" r="3" />
    </svg>
  );
}

export default async function Home() {
  const { games, rankings, lineMovement, apiStatus, apiDetail } = await getApiSnapshot();

  return (
    <main className="shell visual-home">
      <AppHeader apiStatus={apiStatus} apiDetail={apiDetail} title="Silvers Syndicate" />

      <section className="visual-hero">
        <div className="hero-copy-stack">
          <div className="promo-line">
            <span>✦ 7-day free trial</span>
            <small>Pro access preview</small>
          </div>
          <h2>See where your picks start to <span>break down.</span></h2>
          <p>
            Silvers Syndicate checks line movement, steam, resistance, and weak-leg risk with a clean visual system built around sport equipment icons and custom jersey-style team badges.
          </p>
          <div className="hero-actions">
            <Link className="primary-button large" href="/register?promo=free-week" style={{ textDecoration: 'none' }}>Start Free Week →</Link>
            <Link className="ghost-button large" href="/picks-audit" style={{ textDecoration: 'none' }}>Test Your Picks →</Link>
          </div>
          <div className="hero-trust-row">
            <span>▣ Equipment icons</span>
            <span>◷ Jersey-style badges</span>
            <span>◇ No official logos</span>
          </div>
        </div>

        <aside className="analysis-board glass-card" aria-label="Silvers Syndicate analysis board preview">
          <div className="board-head">
            <strong>Silvers Syndicate Analysis Board</strong>
            <span>● Live</span>
          </div>
          <div className="board-columns">
            <span>Matchup</span>
            <span>Line movement</span>
            <span>Signal</span>
            <span>Risk</span>
          </div>
          {featuredMatchups.map((matchup) => (
            <div className="board-row" key={`${matchup.home.abbr}-${matchup.away.abbr}`}>
              <div className="board-teams">
                <SportEquipmentIcon slug={matchup.sport} size="small" />
                <TeamJerseyBadge abbr={matchup.home.abbr} tone={matchup.home.tone} number={matchup.home.number} size="small" />
                <div>
                  <strong>{matchup.home.abbr}</strong>
                  <span>{matchup.home.name}</span>
                </div>
                <b>vs</b>
                <TeamJerseyBadge abbr={matchup.away.abbr} tone={matchup.away.tone} number={matchup.away.number} size="small" />
                <div>
                  <strong>{matchup.away.abbr}</strong>
                  <span>{matchup.away.name}</span>
                </div>
              </div>
              <MiniSparkline tone={matchup.riskTone} />
              <span className={`signal signal-${matchup.signal.toLowerCase().replace(/\s+/g, '_')}`}>{matchup.signal}</span>
              <span className={`risk risk-${matchup.riskTone}`}>{matchup.risk}</span>
            </div>
          ))}
          <p className="board-disclaimer">Custom equipment icons and jersey-style badges are for analysis only. No league, team, sportsbook, or data-provider affiliation.</p>
        </aside>
      </section>

      <SportIconStrip />

      <section className="visual-section">
        <div className="section-heading">
          <p className="eyebrow blue">What we check before you build</p>
          <h3>A cleaner way to question your own ticket.</h3>
        </div>
        <div className="icon-feature-grid">
          {checkCards.map((card) => (
            <article className="icon-feature-card" key={card.title}>
              <SportEquipmentIcon slug={card.sport} />
              <h4>{card.title}</h4>
              <p>{card.copy}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="split-analytics-row">
        <div className="panel visual-line-panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Line movement over time</p>
              <h3>BUF vs MIA · spread pressure</h3>
            </div>
            <span className="analysis-chip">Last 15 min pull: 0.5 pts</span>
          </div>
          <LineMovementGraph data={lineMovement} />
        </div>

        <div className="panel pick-vs-market">
          <p className="eyebrow">Your pick vs. market check</p>
          <h3>The ticket may feel right. The market may disagree.</h3>
          <div className="compare-row">
            <div>
              <span><TeamJerseyBadge abbr="BUF" tone="blue" number="17" size="small" /> Your pick</span>
              <strong>This favorite feels safe.</strong>
            </div>
            <b>→</b>
            <div>
              <span><SportEquipmentIcon slug="nfl" size="small" /> Market check</span>
              <strong>Resistance is building across books.</strong>
            </div>
          </div>
          <div className="compare-row warning-row">
            <div>
              <span><TeamJerseyBadge abbr="DAL" tone="silver" number="4" size="small" /> Your pick</span>
              <strong>This 3-leg parlay looks clean.</strong>
            </div>
            <b>→</b>
            <div>
              <span><SportEquipmentIcon slug="cfb" size="small" /> Weak-leg check</span>
              <strong>One leg is carrying most of the failure risk.</strong>
            </div>
          </div>
          <Link className="primary-button" href="/picks-audit" style={{ textDecoration: 'none' }}>Run a Pick Audit</Link>
        </div>
      </section>

      <section className="visual-section">
        <div className="panel-header">
          <div>
            <p className="eyebrow blue">Today’s top matchups & signals</p>
            <h3>Team names, abbreviations, custom jersey badges, and market tags.</h3>
          </div>
          <Link className="ghost-button" href="/sports" style={{ textDecoration: 'none' }}>View full board →</Link>
        </div>
        <div className="matchup-card-grid">
          {featuredMatchups.map((matchup) => (
            <article className="matchup-preview-card" key={`${matchup.league}-${matchup.home.abbr}-${matchup.away.abbr}`}>
              <div className="game-topline"><span className="league-chip"><SportEquipmentIcon slug={matchup.sport} size="small" /> {matchup.league}</span><span>{matchup.time}</span></div>
              <div className="matchup-teams-row">
                <div><TeamJerseyBadge abbr={matchup.home.abbr} tone={matchup.home.tone} number={matchup.home.number} /><strong>{matchup.home.abbr}</strong><span>{matchup.home.name}</span></div>
                <b>vs</b>
                <div><TeamJerseyBadge abbr={matchup.away.abbr} tone={matchup.away.tone} number={matchup.away.number} /><strong>{matchup.away.abbr}</strong><span>{matchup.away.name}</span></div>
              </div>
              <p>{matchup.market}</p>
              <div className="signal-row">
                <span className={`signal signal-${matchup.signal.toLowerCase().replace(/\s+/g, '_')}`}>{matchup.signal}</span>
                <span className={`risk risk-${matchup.riskTone}`}>{matchup.risk}</span>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="visual-section learn-section">
        <div className="section-heading centered">
          <p className="eyebrow blue">Learn. Analyze. Bet smarter.</p>
          <h3>Organic content that answers what serious fans already search for.</h3>
        </div>
        <div className="guide-card-grid">
          {guideCards.map((card) => (
            <article className="guide-card" key={card.title}>
              <SportEquipmentIcon slug={card.sport} />
              <h4>{card.title}</h4>
              <p>{card.copy}</p>
              <Link href="/methodology" style={{ textDecoration: 'none' }}>Read guide →</Link>
            </article>
          ))}
        </div>
      </section>

      <section className="visual-section faq-grid">
        {[
          ['Is there a free trial?', 'Yes. New launch members get the first week free.'],
          ['Do you offer picks?', 'No. We provide market intelligence. You make the call.'],
          ['Can I cancel anytime?', 'Yes. Cancel during the free week or keep Core or Pro.'],
          ['Are you affiliated with teams or leagues?', 'No. Team references and custom icons are used for analysis and navigation only.']
        ].map(([question, answer]) => (
          <details className="faq-card" key={question}>
            <summary>{question}</summary>
            <p>{answer}</p>
          </details>
        ))}
      </section>

      <section className="trust-cta-strip">
        <span>10,000+ market checks simulated</span>
        <span>Real-time market data mindset</span>
        <span>Bankroll protection first</span>
        <Link className="primary-button" href="/register?promo=free-week" style={{ textDecoration: 'none' }}>Start your 7-day free trial →</Link>
      </section>

      <PaidPreviewGate title="Unlock the full market board">
        <section className="content-grid">
          <div className="panel slate-panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Premium board</p>
                <h3>Full game list and market notes</h3>
              </div>
            </div>
            <div className="game-list">
              {games.map((game) => <GameCard game={game} key={game.id} />)}
            </div>
          </div>

          <aside className="panel rank-panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">8-combo ranking</p>
                <h3>Containment zone</h3>
              </div>
            </div>
            <div className="rank-list">
              {rankings.map((ranking) => <RankingCard ranking={ranking} key={ranking.rank} />)}
            </div>
          </aside>
        </section>
      </PaidPreviewGate>
    </main>
  );
}
