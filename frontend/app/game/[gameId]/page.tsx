import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { SignalPill } from '@/components/SignalPill';
import { LineMovementGraph } from '@/components/LineMovementGraph';
import { getSportSlugForLeague } from '@/lib/sports';

export default async function GameDetailPage({ params }: { params: { gameId: string } }) {
  const { games, lineMovement, apiStatus, apiDetail } = await getApiSnapshot();
  const game = games.find((item) => item.id === params.gameId);

  if (!game) notFound();

  const sportSlug = getSportSlugForLeague(game.league || game.sport_key);

  return (
    <main className="shell">
      <AppHeader title="Game Detail" apiStatus={apiStatus} apiDetail={apiDetail} />

      <section className="panel" style={{ marginBottom: 18 }}>
        <div className="game-topline"><span className="league-chip">{game.league}</span><span>{game.start}</span><span className="data-status">{game.status_label || 'Live'}</span></div>
        <h2 style={{ marginBottom: 12 }}>{game.matchup}</h2>
        <p className="movement">{game.movement}</p>
        <div className="hero-actions">
          <Link className="ghost-button" href={`/sports/${sportSlug}`} style={{ textDecoration: 'none' }}>Back to Market Board</Link>
          <Link className="inqsi-primary" href="/parlays" style={{ textDecoration: 'none' }}>Build With This Game</Link>
        </div>
      </section>

      <section className="panel" style={{ marginBottom: 18 }}>
        <div className="panel-header"><div><p className="eyebrow">Live Market Snapshot</p><h3>Moneyline, spread, and total</h3></div></div>
        <div className="market-row">
          <div><span>Favorite</span><strong>{game.favorite}</strong><b>{game.favoriteMl || game.favorite_ml || 'Waiting'}</b></div>
          <div><span>Underdog</span><strong>{game.underdog}</strong><b>{game.underdogMl || game.underdog_ml || 'Waiting'}</b></div>
          <div><span>Spread</span><strong>Line</strong><b>{game.spread || 'Waiting'}</b></div>
          <div><span>Total</span><strong>O/U</strong><b>{game.total || 'Waiting'}</b></div>
        </div>
      </section>

      <section className="status-row">
        <article className="status-card"><span>Favorite</span><strong>{game.favorite}</strong><p>Primary favorite from current board.</p></article>
        <article className="status-card"><span>Underdog</span><strong>{game.underdog}</strong><p>Secondary side from current board.</p></article>
        <article className="status-card"><span>Book Count</span><strong>{game.bookCount || 'Live'}</strong><p>Market sources represented.</p></article>
        <article className="status-card"><span>Risk</span><strong>{game.risk}</strong><p>{game.confidence}</p></article>
      </section>

      <section className="content-grid" style={{ marginTop: 18 }}>
        <div className="panel">
          <div className="panel-header"><div><p className="eyebrow">Market Signals</p><h3>Signal types detected</h3></div></div>
          <div className="signal-row" style={{ marginBottom: 14 }}>
            {game.signals.map((signal) => <SignalPill signal={signal} key={signal} />)}
          </div>
          <p className="movement">{game.marketNote ?? 'Signals reflect market movement only. They do not guarantee outcomes and they do not replace refusal rules.'}</p>
        </div>
        <aside className="panel">
          <p className="eyebrow">Build Gate</p>
          <h3>Eligibility review</h3>
          <p className="movement">Eligible only if the rest of the slate preserves anchor discipline and avoids forced confidence.</p>
          <Link className="inqsi-primary" href="/parlays" style={{ textDecoration: 'none', width: '100%' }}>Open Parlays</Link>
        </aside>
      </section>

      <LineMovementGraph data={lineMovement} />
    </main>
  );
}
