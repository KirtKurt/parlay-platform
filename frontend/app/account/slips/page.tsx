import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { TeamJerseyBadge } from '@/components/SportVisuals';
import { MySlipsScoresDashboard } from '@/components/MySlipsScoresDashboard';

export const metadata = {
  title: 'My Slips & Scores',
  description: 'Control slip visibility, review post-game analysis, and track InQsi accuracy scores over time.',
  alternates: { canonical: '/account/slips' }
};

export default function MySlipsPage() {
  return (
    <main className="shell">
      <AppHeader title="My Slips & Scores" />

      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Saved slips</p>
        <div className="team-badge-row" style={{ marginTop: 10 }}>
          <TeamJerseyBadge abbr="PUB" tone="gold" number="1" />
          <TeamJerseyBadge abbr="PRI" tone="blue" number="0" />
          <TeamJerseyBadge abbr="ACC" tone="teal" number="%" />
        </div>
        <h2>Choose what stays private and what can be shown publicly.</h2>
        <p className="hero-copy">Save slips privately by default, choose which slips can be displayed publicly, choose which score percentages appear on the public profile card, and review post-game analysis after the games are final. Other customers cannot comment on slips for now.</p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/parlays" style={{ textDecoration: 'none' }}>Build My Slip</Link>
          <Link className="ghost-button large" href="/parlay-scanner" style={{ textDecoration: 'none' }}>Scan My Slip</Link>
          <Link className="ghost-button large" href="/account" style={{ textDecoration: 'none' }}>Back to Account</Link>
        </div>
      </section>

      <MySlipsScoresDashboard />
    </main>
  );
}
