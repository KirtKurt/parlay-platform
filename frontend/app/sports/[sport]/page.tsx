import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { GameCard } from '@/components/GameCard';
import { PaidPreviewGate } from '@/components/PaidPreviewGate';
import { ContentBlock } from '@/components/ContentBlock';
import { getSportBySlug, getSportSlugForLeague, sports } from '@/lib/sports';

export function generateStaticParams() {
  return sports.map((sport) => ({ sport: sport.slug }));
}

export default async function SportPage({ params }: { params: { sport: string } }) {
  const sport = getSportBySlug(params.sport);
  if (!sport) notFound();

  const { games, rankings, apiStatus, apiDetail } = await getApiSnapshot();
  const visibleGames = games.filter((game) => getSportSlugForLeague(game.league) === sport.slug);

  return (
    <main className="shell">
      <AppHeader title={sport.title} apiStatus={apiStatus} apiDetail={apiDetail} />

      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">{sport.label} board · first week free</p>
        <h2>{sport.title}</h2>
        <p className="hero-copy">
          {sport.description} Use this page to get oriented fast: what is available, where the movement is, and which matchups are worth opening for a deeper look.
        </p>
        <div className="hero-actions">
          <Link className="primary-button large" href={`/register?promo=free-week&sport=${sport.slug}`} style={{ textDecoration: 'none' }}>Unlock {sport.label}</Link>
          <Link className="ghost-button large" href="/sports" style={{ textDecoration: 'none' }}>Back to sports</Link>
          <Link className="ghost-button large" href="/login" style={{ textDecoration: 'none' }}>Log In</Link>
        </div>
      </section>

      <section className="status-row">
        <article className="status-card"><span>Preview</span><strong>{visibleGames.length || 'Ready'}</strong><p>You can see whether a {sport.label} slate is available before logging in.</p></article>
        <article className="status-card"><span>Signals</span><strong>Preview</strong><p>We show the signal types. Members unlock the reason behind them.</p></article>
        <article className="status-card"><span>Movement</span><strong>Locked</strong><p>The full line history unlocks after registration.</p></article>
        <article className="status-card"><span>Promo</span><strong>7 days</strong><p>New members can start with the first week free.</p></article>
      </section>

      <ContentBlock
        eyebrow={`${sport.label} guide`}
        title={`A simpler way to read the ${sport.label} board`}
        body={`The ${sport.label} board is built to show more than a final price. It helps you see whether a matchup is getting support, running into resistance, or becoming too unstable to force.`}
        items={[
          { title: 'Timing matters', detail: 'Early movement is useful, but later confirmation tells the better story.' },
          { title: 'Signals stay readable', detail: 'Steam, resistance, coin-flip, chaos, and anomaly labels stay consistent across sports.' },
          { title: 'Matchup pages', detail: 'Each game or match can grow into its own timeline with notes and signal history.' },
          { title: 'Member unlock', detail: 'Public preview stays simple. Full ranking and movement detail unlock after registration.' }
        ]}
      />

      <PaidPreviewGate title={`Unlock the full ${sport.label} board`}>
        <section className="content-grid">
          <div className="panel slate-panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Market Board</p>
                <h3>{visibleGames.length ? `${visibleGames.length} active game${visibleGames.length === 1 ? '' : 's'}` : 'No active slate yet'}</h3>
              </div>
            </div>
            <div className="game-list">
              {visibleGames.length ? visibleGames.map((game) => <GameCard game={game} key={game.id} />) : (
                <article className="game-card">
                  <h4>{sport.label} board is ready</h4>
                  <p className="movement">When the backend sends {sport.label} games or matches, this page will fill in automatically with cards, signals, and links to detail pages.</p>
                </article>
              )}
            </div>
          </div>

          <aside className="panel rank-panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Build Discipline</p>
                <h3>Rules enforced</h3>
              </div>
            </div>
            <div className="rank-list">
              <article className="rank-card top-zone">
                <div className="rank-head"><span>Gate</span><b>LOCKED</b></div>
                <h4>At least 2 Strong Solid legs</h4>
                <p>No forced structure. If the board does not support a safe build, the app should refuse instead of dressing it up.</p>
              </article>
              <article className="rank-card">
                <div className="rank-head"><span>Containment</span><b>TOP-3</b></div>
                <h4>{rankings[0]?.structure ?? 'MIXED 2-SOLID-1-CF'}</h4>
                <p>{rankings[0]?.note ?? 'Ranked output is built around containment, not pick-selling.'}</p>
              </article>
            </div>
          </aside>
        </section>
      </PaidPreviewGate>
    </main>
  );
}
