import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { ContentBlock } from '@/components/ContentBlock';
import { SportEquipmentIcon, SportHeroPanel, SportIconStrip, TeamJerseyBadge } from '@/components/SportVisuals';

const sections = [
  ['Market capture', 'The board starts with verified market data before trying to explain it.', 'nfl'],
  ['Movement path', 'Repeated checks show whether a move is strengthening, stalling, or starting to fall apart.', 'nba'],
  ['Market support', 'A side is gaining market support. It still has to survive confirmation before it earns trust.', 'mlb'],
  ['Resistance', 'The market is pushing back. That can weaken a pick or turn it into a coin-flip spot.', 'nhl'],
  ['Coin flip', 'A spot with real uncertainty: compressed pricing, conflicting signals, late movement, or news sensitivity.', 'tennis'],
  ['Market Anomaly', 'Something about the price action looks unusual enough to flag for review.', 'darts'],
  ['Refusal', 'If the board is too messy, InQsi should slow you down instead of dressing up a risky answer.', 'lacrosse']
] as const;

export default function MethodologyPage() {
  return (
    <main className="shell">
      <AppHeader title="Methodology" />
      <section className="sport-hero-grid">
        <div className="hero-card glass-card" style={{ minHeight: 0 }}>
          <p className="eyebrow blue">How InQsi checks the board · 5 days free</p>
          <h2>Before you lock it in, see what the market is warning you about.</h2>
          <p className="hero-copy">
            InQsi keeps the review simple: bring the picks you already like, then check the signals that could make them risky. You see movement, pressure, and warning signs in plain English so you can slow down, rethink weak legs, and make a more informed decision.
          </p>
          <div className="team-badge-row" style={{ marginTop: 16 }}>
            <TeamJerseyBadge abbr="UGA" tone="red" number="1" />
            <b>vs</b>
            <TeamJerseyBadge abbr="ALA" tone="crimson" number="15" />
            <span style={{ color: '#96a4bd', fontSize: '.85rem' }}>Example game review</span>
          </div>
          <div className="hero-actions">
            <Link className="primary-button large" href="/register?promo=5-days" style={{ textDecoration: 'none' }}>Start 5 Days Free</Link>
            <Link className="ghost-button large" href="/sports" style={{ textDecoration: 'none' }}>View Sports</Link>
          </div>
        </div>
        <SportHeroPanel sportSlug="cfb" title="Clear signals. No fake certainty." copy="You should not need a trading desk to understand the board. InQsi keeps the risk check direct, readable, and focused on the decision in front of you." />
      </section>
      <SportIconStrip compact />
      <section className="status-row">
        {sections.slice(0, 4).map(([title, detail, sport]) => (
          <article className="status-card" key={title}><SportEquipmentIcon slug={sport} /><span>Rule</span><strong>{title}</strong><p>{detail}</p></article>
        ))}
      </section>
      <section className="status-row">
        {sections.slice(4).map(([title, detail, sport]) => (
          <article className="status-card" key={title}><SportEquipmentIcon slug={sport} /><span>Rule</span><strong>{title}</strong><p>{detail}</p></article>
        ))}
      </section>
      <ContentBlock
        eyebrow="Plain-English glossary"
        title="The words on the board should actually help"
        body="Signals should make the risk easier to understand, not make the product sound complicated. When you see resistance, chaos, or a coin-flip marker, the point is simple: pause, review the weak spot, and decide if the pick still deserves a place on your slip."
        items={[
          { title: 'Anchor', detail: 'A stronger candidate only after the market gives it enough support.' },
          { title: 'Variable', detail: 'The spot carrying the most uncertainty in a ranked structure.' },
          { title: 'Chaos', detail: 'A warning that the board may be too unstable to trust.' },
          { title: 'Human gate', detail: 'A final review layer for fragile spots before trusting a structure.' }
        ]}
      />
    </main>
  );
}
