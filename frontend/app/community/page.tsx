import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';

const cards = [
  { h: 'inqsi-member', n: 'InQsi Member', w: 67, m: 63, f: true },
  { h: 'buffalo-market', n: 'Buffalo Market Read', w: 71, m: 58, f: false },
  { h: 'three-leg-only', n: 'Three Leg Only', w: 62, m: 66, f: false }
];

export default function Page() {
  const title = 'Followed Profiles';
  const text = 'Find public scorecards by handle. Customers stay private by default and choose what score percentages show.';
  return (
    <main className="shell">
      <AppHeader title={title} />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">{title}</p>
        <h2>{'Find people by handle.'}</h2>
        <p className="hero-copy">{text}</p>
      </section>
      <section className="panel">
        <div className="game-list">
          {cards.map((card) => (
            <Link className="game-card" href={`/u/${card.h}`} key={card.h} style={{ color: 'inherit', textDecoration: 'none' }}>
              <div className="game-topline"><span className="league-chip">@{card.h}</span><span>{card.f ? 'Followed' : 'Not followed'}</span></div>
              <h4>{card.n}</h4>
              <p className="movement">1 week: {card.w}% · 1 month: {card.m}%</p>
            </Link>
          ))}
        </div>
      </section>
    </main>
  );
}
