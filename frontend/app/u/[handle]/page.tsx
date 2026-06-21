import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://inqsi.app';
const profileSchemaType = ['Profile', 'Page'].join('');

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
  const profileJsonLd = {
    '@context': 'https://schema.org',
    '@type': profileSchemaType,
    name: `${card.name} member score card`,
    url: `${siteUrl}/u/${card.handle}`,
    about: 'InQsi member score card'
  };

  return (
    <main className="shell">
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(profileJsonLd) }} />
      <AppHeader title="Member Score Card" />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Followed member</p>
        <h2>{card.name}</h2>
        <p className="hero-copy">@{card.handle} shares an optional member score card. Score display is controlled by the member.</p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/account/slips" style={{ textDecoration: 'none' }}>Open My Slips</Link>
          <Link className="ghost-button large" href="/account" style={{ textDecoration: 'none' }}>Back to Account</Link>
        </div>
      </section>
      <section className="status-row">
        <article className="status-card"><span>1 week</span><strong>{card.week}%</strong><p>Shown when the member allows.</p></article>
        <article className="status-card"><span>1 month</span><strong>{card.month}%</strong><p>Shown when the member allows.</p></article>
        <article className="status-card"><span>Interaction</span><strong>Quiet</strong><p>No public comments at launch.</p></article>
      </section>
    </main>
  );
}
