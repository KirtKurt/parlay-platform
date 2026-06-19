import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { ContentBlock } from '@/components/ContentBlock';

const sections = [
  ['T1 baseline', 'The first clean capture. We record the board before trying to explain it.'],
  ['T2/T3 confirmation', 'Later snapshots show whether the early move held up or started to fall apart.'],
  ['Steam', 'A side is gaining market support. It still has to survive confirmation before we treat it seriously.'],
  ['Resistance', 'The market is pushing back. That can weaken a leg or turn it into a coin-flip spot.'],
  ['Coin flip', 'A leg with real uncertainty: compressed pricing, conflicting signals, late movement, or news sensitivity.'],
  ['Market Anomaly', 'Something about the price action looks unusual enough to flag for review.'],
  ['Refusal', 'If the board is too messy, the system should say no instead of forcing a fake-safe answer.']
];

export default function MethodologyPage() {
  return (
    <main className="shell">
      <AppHeader title="Methodology" />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">How the board thinks · first week free</p>
        <h2>We do not start with a pick. We start with the movement.</h2>
        <p className="hero-copy">
          Silvers Syndicate is built around a simple idea: the market leaves clues. The goal is to capture the board at the right times,
          compare how books move, and explain which games look clean, fragile, or too chaotic to trust.
        </p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/register?promo=free-week" style={{ textDecoration: 'none' }}>Start Free Week</Link>
          <Link className="ghost-button large" href="/sports" style={{ textDecoration: 'none' }}>View Sports</Link>
        </div>
      </section>
      <section className="status-row">
        {sections.slice(0, 4).map(([title, detail]) => (
          <article className="status-card" key={title}><span>Rule</span><strong>{title}</strong><p>{detail}</p></article>
        ))}
      </section>
      <section className="status-row">
        {sections.slice(4).map(([title, detail]) => (
          <article className="status-card" key={title}><span>Rule</span><strong>{title}</strong><p>{detail}</p></article>
        ))}
      </section>
      <ContentBlock
        eyebrow="Plain-English glossary"
        title="The words on the board should actually mean something"
        body="A signal is only useful if you can understand why it is there. That is why the same language repeats across the product. When you see steam, resistance, chaos, or a coin-flip marker, the point is not to sound technical. The point is to make the risk easier to read."
        items={[
          { title: 'Anchor', detail: 'A stronger candidate only after the market confirms it across the right window.' },
          { title: 'Variable', detail: 'The leg carrying the most uncertainty in a ranked structure.' },
          { title: 'Chaos', detail: 'A warning that the board is unstable enough to slow down or refuse the build.' },
          { title: 'Human gate', detail: 'A final review layer for fragile spots before trusting a structure.' }
        ]}
      />
    </main>
  );
}
