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
        <p className="eyebrow blue">{sport.label} market board · first week free</p>
        <h2>{sport.title}</h2>
        <p className="hero-copy">
          {sport.description} This page gives visitors a clean view of market movement, game-level signals, slate status, and member-only research depth.
        </p>
        <div className="hero-actions">
          <Link className="primary-button large" href={`/register?promo=free-week&sport=${sport.slug}`} style={{ textDecoration: 'none' }}>Unlock {sport.label}</Link>
          <Link className="ghost-button large" href="/sports" style={{ textDecoration: 'none' }}>Back to sports</Link>
          <Link className="ghost-button large" href="/login" style={{ textDecoration: 'none' }}>Log In</Link>
        </div>
      </section>

      <section className="status-row">
        <article className="status-card"><span>Preview</span><strong>{visibleGames.length || 'Ready'}</strong><p>{sport.label} slate count is visible before login.</p></article>
        <article className="status-card"><span>Signals</span><strong>Teased</strong><p>High-level signal types are visible. Full reason detail is gated.</p></article>
        <article className="status-card"><span>Movement</span><strong>Locked</strong><p>Full line history unlocks after registration.</p></article>
        <article className="status-card"><span>Promo</span><strong>7 days</strong><p>New launch members can start with the first week free.</p></article>
      </section>

      <ContentBlock
        eyebrow={`${sport.label} guide`}
        title={`How the ${sport.label} board helps with market research`}
        body={`The ${sport.label} board explains what changed in the market instead of only showing a final number. It is structured for discovery around ${sport.label} line movement, market signals, slate monitoring, and game-level notes.`}
        items={[
          { title: 'Snapshot discipline', detail: 'The page is prepared for T1, T2, T3, T4, and T5 movement context.' },
          { title: 'Signal clarity', detail: 'Steam, resistance, coin-flip, chaos, and anomaly language is consistent across sports.' },
          { title: 'Game pages', detail: 'Each matchup can become a detail page with its own timeline and explanation.' },
          { title: 'Member unlock', detail: 'Preview stays public; full ranking and movement detail unlock after registration.' }
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
                  <h4>{sport.label} data pipeline ready</h4>
                  <p className="movement">When the backend returns {sport.label} games or matches, this page will populate automatically with cards, signals, and links to detail pages.</p>
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
                <p>Zero forced structure. If a safe build cannot be created, the app refuses instead of increasing exposure.</p>
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
