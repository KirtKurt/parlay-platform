import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { ContentBlock } from '@/components/ContentBlock';

const sections = [
  ['T1 baseline', 'Immutable capture of both-side moneyline, spread, and total for every game and sportsbook. No inference at T1.'],
  ['T2/T3 confirmation', 'Market movement is compared through later snapshots. Fanatics T3 is canonical; FanDuel and DraftKings act as comparators.'],
  ['Steam', 'A price-strengthening signal that can support an anchor only when it survives multi-book confirmation.'],
  ['Resistance', 'A warning that a side is not moving cleanly, often weakening anchor quality or creating a coin-flip condition.'],
  ['Coin flip', 'A controlled variable leg with real uncertainty: compression, conflicting signals, late instability, or information sensitivity.'],
  ['Market Anomaly', 'Unusual price behavior flag. It describes market data only and does not make claims about people, teams, or intent.'],
  ['Refusal', 'If a compliant build cannot be created, the system refuses instead of inventing a safer-looking answer.']
];

export default function MethodologyPage() {
  return (
    <main className="shell">
      <AppHeader title="Methodology" />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Trust layer · first week free</p>
        <h2>Understand the signal before you trust the board.</h2>
        <p className="hero-copy">
          Silvers Syndicate explains the market structure behind each board: where the line moved, which books agreed, which legs are anchors, and where uncertainty lives. The methodology page is written to help users and search engines understand the terms behind the product.
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
        title="How to read Silvers Syndicate signals"
        body="The system is designed around repeatable language. A user should be able to read a sport page, game page, line movement graph, and market board without guessing what the signal labels mean. That same consistency helps organic discovery because the site gives clear definitions for sports market intelligence terms."
        items={[
          { title: 'Anchor', detail: 'A stronger candidate only after movement confirms across the correct snapshot window.' },
          { title: 'Variable', detail: 'The side or leg carrying the most uncertainty in a ranked structure.' },
          { title: 'Chaos', detail: 'A warning that instability is high enough to slow down or refuse a build.' },
          { title: 'Human gate', detail: 'A review layer for fragile spots before a final structure is trusted.' }
        ]}
      />
    </main>
  );
}
