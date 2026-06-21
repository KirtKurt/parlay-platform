import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';

const rows = [
  ['Buffalo Market Read', 71, 58],
  ['InQsi Member', 67, 63],
  ['Three Leg Only', 62, 66]
];

export default function Page() {
  return (
    <main className="shell">
      <AppHeader title="Rankings" />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Leaderboard foundation</p>
        <h2>Rank public cards by rolling percent.</h2>
        <p className="hero-copy">This is the future ranking foundation. Only owner-approved cards should appear here.</p>
      </section>
      <section className="panel">
        <div className="game-list">
          {rows.map(([name, week, month]) => (
            <article className="game-card" key={String(name)}>
              <div className="game-topline"><span className="league-chip">{week}% week</span><span>{month}% month</span></div>
              <h4>{name}</h4>
            </article>
          ))}
        </div>
        <Link className="ghost-button" href="/community" style={{ display: 'inline-block', marginTop: 16, textDecoration: 'none' }}>Back to Followed Profiles</Link>
      </section>
    </main>
  );
}
