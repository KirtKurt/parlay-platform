import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';

const cards = [
  { handle: 'inqsi-member', name: 'InQsi Member', week: 67, month: 63 },
  { handle: 'buffalo-market', name: 'Buffalo Market Read', week: 71, month: 58 },
  { handle: 'three-leg-only', name: 'Three Leg Only', week: 62, month: 66 }
];

export function generateStaticParams() {
  return cards.map((card) => ({ handle: card.handle }));
}

export default function Page({ params }: { params: { handle: string } }) {
  const card = cards.find((item) => item.handle === params.handle) ?? cards[0];

  return (
    <main className="shell">
      <AppHeader title="Public Score Card" />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Followed profile</p>
        <h2>{card.name}</h2>
        <p className="hero-copy">@{card.handle} shares an optional public score card. Comments and messages are off.</p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/account/slips" style={{ textDecoration: 'none' }}>Open My Slips</Link>
          <Link className="ghost-button large" href="/account" style={{ textDecoration: 'none' }}>Back to Account</Link>
        </div>
      </section>
      <section className="status-row">
        <article className="status-card"><span>1 week</span><strong>{card.week}%</strong><p>Shown when owner allows.</p></article>
        <article className="status-card"><span>1 month</span><strong>{card.month}%</strong><p>Shown when owner allows.</p></article>
        <article className="status-card"><span>Comments</span><strong>Off</strong><p>No public comments at launch.</p></article>
      </section>
    </main>
  );
}
