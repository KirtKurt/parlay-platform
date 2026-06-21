import Link from 'next/link';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { RankingCard } from '@/components/RankingCard';

export default async function ParlaysPage() {
  const { rankings } = await getApiSnapshot();

  return (
    <main className="shell">
      <AppHeader title="AI Slip Builder" />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">AI Slip Builder</p>
        <h2>Build a smarter 3-leg slip before you lock it in.</h2>
        <p className="hero-copy">Choose the games you like and let InQsi challenge the structure. The builder is capped at 3 legs, looks for strong anchors, spots the coin-flip leg, checks overlap, and warns you when the market is not giving the slip enough support.</p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/parlays/build" style={{ textDecoration: 'none' }}>Start building</Link>
          <Link className="ghost-button large" href="/parlay-scanner" style={{ textDecoration: 'none' }}>Scan an existing slip</Link>
          <Link className="ghost-button large" href="/sports" style={{ textDecoration: 'none' }}>Review sports board</Link>
        </div>
      </section>

      <section className="content-grid">
        <div className="panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Builder preview</p>
              <h3>How InQsi ranks a 3-leg slip</h3>
            </div>
          </div>
          <p className="movement">A 3-leg slip has eight possible outcome paths. InQsi ranks the structure so you can see which version looks strongest, where the weak leg may be hiding, and whether the market is warning you to slow down.</p>
          <div className="rank-list">
            {rankings.map((ranking) => <RankingCard ranking={ranking} key={ranking.rank} />)}
          </div>
        </div>

        <aside className="panel rank-panel">
          <div className="panel-header compact">
            <div>
              <p className="eyebrow">Core discipline</p>
              <h3>3 legs maximum. Do not force the slip.</h3>
            </div>
          </div>
          <p className="movement"><b>3-leg cap:</b> InQsi parlay builds do not go beyond three legs.</p>
          <p className="movement"><b>Anchors:</b> the legs with the cleanest market support.</p>
          <p className="movement"><b>Coin-flip leg:</b> the leg that needs extra caution because the market is less stable.</p>
          <p className="movement"><b>Zero-overlap:</b> when building more than one slip, InQsi avoids repeating the same team across builds.</p>
          <p className="movement"><b>Refusal discipline:</b> if the market does not support the structure, InQsi should tell you instead of dressing up a weak slip.</p>
        </aside>
      </section>
    </main>
  );
}
