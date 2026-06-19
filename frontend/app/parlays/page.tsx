import Link from 'next/link';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { RankingCard } from '@/components/RankingCard';

export default async function ParlaysPage() {
  const { rankings, apiStatus, apiDetail } = await getApiSnapshot();

  return (
    <main className="shell">
      <AppHeader title="Parlay workspace" apiStatus={apiStatus} apiDetail={apiDetail} />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Ranked containment</p>
        <h2>Builds are ranked around risk structure, not hype.</h2>
        <p className="hero-copy">The parlay workspace shows the Top-3 containment zone, anchor legs, coin-flip variables, rejected games, and refusal reasons when the slate is unsafe.</p>
        <Link className="primary-button large" href="/parlays/build" style={{ textDecoration: 'none' }}>Start build</Link>
      </section>
      <section className="content-grid">
        <div className="panel">
          <div className="panel-header"><div><p className="eyebrow">Current Demo Build</p><h3>8-combo ranking</h3></div></div>
          <div className="rank-list">
            {rankings.map((ranking) => <RankingCard ranking={ranking} key={ranking.rank} />)}
          </div>
        </div>
        <aside className="panel rank-panel">
          <div className="panel-header compact"><div><p className="eyebrow">Locked rules</p><h3>Non-forcing principle</h3></div></div>
          <p className="movement">Never increase risk to fill a structure. If the market does not produce at least two strong solid anchors, the build must refuse.</p>
          <p className="movement">Zero-overlap mode prevents duplicated teams across multiple customer parlays.</p>
        </aside>
      </section>
    </main>
  );
}
