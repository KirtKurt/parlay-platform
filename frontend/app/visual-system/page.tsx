import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { SportIconStrip, TeamJerseyBadge, SportEquipmentIcon, TeamBadgeRow } from '@/components/SportVisuals';

export const metadata = {
  title: 'Visual System Check | Silvers Syndicate',
  description: 'A live deployment check for the Silvers Syndicate sport equipment icons and jersey-style team badge system.'
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
      <AppHeader title="Visual system check" />

      <section className="sport-hero-grid">
        <div className="hero-card glass-card" style={{ minHeight: 0 }}>
          <p className="eyebrow blue">Deployment check · equipment icons · jersey badges</p>
          <h2>If you can see this page, the new visual system is live.</h2>
          <p className="hero-copy">
            This page is a direct test route for the new Silvers Syndicate visual system: sports equipment icons, custom jersey-style team badges, and no official league or team logos.
          </p>
          <div className="hero-actions">
            <Link className="primary-button large" href="/sports" style={{ textDecoration: 'none' }}>Open Sports Board</Link>
            <Link className="ghost-button large" href="/" style={{ textDecoration: 'none' }}>Back Home</Link>
          </div>
        </div>
        <aside className="sport-hero-panel accent-blue">
          <SportEquipmentIcon slug="nfl" size="large" showLabel />
          <h3>Equipment-first design</h3>
          <p>Every sport gets a clear visual anchor: football, basketball, puck, bat, racket, soccer ball, dartboard, lacrosse stick, or paddle.</p>
          <div className="mini-equipment-line"><span>No logos</span><span>Custom icons</span><span>Jersey badges</span></div>
        </aside>
      </section>

      <SportIconStrip />

      <section className="visual-section">
        <div className="section-heading">
          <p className="eyebrow blue">Jersey-style team markers</p>
          <h3>Team names and abbreviations without official marks.</h3>
        </div>
        <div className="matchup-card-grid">
          {sampleTeams.map(([left, right]) => (
            <article className="matchup-preview-card" key={`${left}-${right}`}>
              <TeamBadgeRow leftTeam={left} rightTeam={right} />
              <p>Custom jersey badges only. No official logos, no league marks, no sportsbook branding.</p>
              <div className="signal-row">
                <span className="signal signal-steam">STEAM</span>
                <span className="risk risk-low">VISUAL CHECK</span>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="trust-cta-strip">
        <span>Visual system version: equipment-and-jersey-v1</span>
        <span>Deployment marker: 2026-06-19</span>
        <span>Not affiliated with any league, team, sportsbook, or data provider.</span>
      </section>
    </main>
  );
}
