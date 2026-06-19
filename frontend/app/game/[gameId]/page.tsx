import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { SignalPill } from '@/components/SignalPill';
import { LineMovementGraph } from '@/components/LineMovementGraph';
import { PaidPreviewGate } from '@/components/PaidPreviewGate';
import { getSportSlugForLeague } from '@/lib/sports';

export default async function GameDetailPage({ params }: { params: { gameId: string } }) {
  const { games, lineMovement, apiStatus, apiDetail } = await getApiSnapshot();
  const game = games.find((item) => item.id === params.gameId);

  if (!game) notFound();

  return (
    <main className="shell">
      <AppHeader title="Game detail terminal" apiStatus={apiStatus} apiDetail={apiDetail} />

      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">{game.league} · {game.start}</p>
        <h2>{game.matchup}</h2>
        <p className="hero-copy">{game.movement}</p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/register" style={{ textDecoration: 'none' }}>Unlock Full Detail</Link>
          <Link className="ghost-button large" href={`/sports/${getSportSlugForLeague(game.league)}`} style={{ textDecoration: 'none' }}>Back to {game.league}</Link>
          <Link className="ghost-button large" href="/login" style={{ textDecoration: 'none' }}>Log In</Link>
        </div>
      </section>

      <section className="status-row">
        <article className="status-card"><span>Favorite</span><strong>{game.favorite}</strong><p>Preview only. Full moneyline path is locked.</p></article>
        <article className="status-card"><span>Underdog</span><strong>{game.underdog}</strong><p>Preview only. Comparator book detail is locked.</p></article>
        <article className="status-card"><span>Total</span><strong>{game.total}</strong><p>O/U market tracked across snapshots.</p></article>
        <article className="status-card"><span>Risk</span><strong>{game.risk}</strong><p>{game.confidence} confidence</p></article>
      </section>

      <section className="panel" style={{ marginBottom: 20 }}>
        <div className="panel-header">
          <div>
            <p className="eyebrow">Free signal preview</p>
            <h3>Signal types detected</h3>
          </div>
        </div>
        <div className="signal-row">
          {game.signals.slice(0, 3).map((signal) => <SignalPill signal={signal} key={signal} />)}
        </div>
      </section>

      <PaidPreviewGate title="Unlock full game movement">
        <section className="content-grid">
          <div className="panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Signals</p>
                <h3>Market explanation</h3>
              </div>
            </div>
            <div className="signal-row" style={{ marginBottom: 16 }}>
              {game.signals.map((signal) => <SignalPill signal={signal} key={signal} />)}
            </div>
            <p className="movement">{game.marketNote ?? 'Signals reflect market movement only. They do not guarantee outcomes and they do not replace the refusal rules.'}</p>
            <div className="rank-list">
              <article className="rank-card top-zone">
                <div className="rank-head"><span>T1</span><b>BASELINE</b></div>
                <h4>Immutable market capture</h4>
                <p>Both-side moneyline, spread, and total are captured without inference.</p>
              </article>
              <article className="rank-card">
                <div className="rank-head"><span>T2/T3</span><b>CONFIRM</b></div>
                <h4>Book agreement and divergence</h4>
                <p>Fanatics is canonical at T3; FanDuel and DraftKings compare agreement, resistance, and magnification.</p>
              </article>
            </div>
          </div>

          <aside className="panel rank-panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Eligibility</p>
                <h3>Build gate</h3>
              </div>
            </div>
            <div className={`risk risk-${game.risk.toLowerCase()}`} style={{ marginBottom: 14 }}>{game.risk} RISK</div>
            <p className="movement">{game.risk === 'HIGH' ? 'High-risk games can be blocked or forced into human gate review.' : 'Eligible only if the rest of the slate preserves anchor discipline.'}</p>
          </aside>
        </section>

        <LineMovementGraph data={lineMovement} />
      </PaidPreviewGate>
    </main>
  );
}
