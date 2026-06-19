import { AppHeader } from '@/components/AppHeader';

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
        <p className="eyebrow blue">Trust layer</p>
        <h2>Risk intelligence, not pick selling.</h2>
        <p className="hero-copy">Silvers Syndicate explains the market structure behind each build: where the line moved, which books agreed, which legs are anchors, and where the uncertainty lives.</p>
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
    </main>
  );
}
