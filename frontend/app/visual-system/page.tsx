import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { SportIconStrip, TeamJerseyBadge, SportEquipmentIcon, TeamBadgeRow } from '@/components/SportVisuals';

export const metadata = {
  title: 'Board Check | InQsi',
  description: 'A live check for the InQsi sports board, matchup cards, and market signal display.'
};

const sampleTeams = [
  ['Buffalo Bills', 'Miami Dolphins'],
  ['Dallas Cowboys', 'Philadelphia Eagles'],
  ['Boston Celtics', 'Los Angeles Lakers'],
  ['Georgia', 'Alabama'],
  ['Maryland', 'Duke'],
  ['Chen', 'Novak']
];

export default function VisualSystemPage() {
  return (
    <main className="shell">
      <AppHeader title="Board check" />

      <section className="sport-hero-grid">
        <div className="hero-card glass-card" style={{ minHeight: 0 }}>
          <p className="eyebrow blue">Live board check · clear sports review</p>
          <h2>If you can see this page, the InQsi board is loading correctly.</h2>
          <p className="hero-copy">
            This page confirms that matchups, sport boards, and market signal cards are readable before a full slate goes live. The goal is simple: make the risk easier to scan before you lock in a pick.
          </p>
          <div className="hero-actions">
            <Link className="primary-button large" href="/sports" style={{ textDecoration: 'none' }}>Open Sports Board</Link>
            <Link className="ghost-button large" href="/" style={{ textDecoration: 'none' }}>Back Home</Link>
          </div>
        </div>
        <aside className="sport-hero-panel accent-blue">
          <SportEquipmentIcon slug="nfl" size="large" showLabel />
          <h3>Fast to read</h3>
          <p>Open the sport, review the matchup, and look for support, resistance, coin-flip pressure, or unusual movement.</p>
          <div className="mini-equipment-line"><span>No logos</span><span>Clear matchups</span><span>Risk signals</span></div>
        </aside>
      </section>

      <SportIconStrip />

      <section className="visual-section">
        <div className="section-heading">
          <p className="eyebrow blue">Matchup examples</p>
          <h3>Game cards should be easy to scan.</h3>
        </div>
        <div className="matchup-card-grid">
          {sampleTeams.map(([left, right]) => (
            <article className="matchup-preview-card" key={`${left}-${right}`}>
              <TeamBadgeRow leftTeam={left} rightTeam={right} />
              <p>Clear matchup labels only. No official logos, no league marks, no sportsbook branding.</p>
              <div className="signal-row">
                <span className="signal signal-steam">STEAM</span>
                <span className="risk risk-low">BOARD CHECK</span>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="trust-cta-strip">
        <span>Board check active</span>
        <span>Deployment marker: 2026-06-19</span>
        <span>Not affiliated with any league, team, sportsbook, or data provider.</span>
      </section>
    </main>
  );
}
