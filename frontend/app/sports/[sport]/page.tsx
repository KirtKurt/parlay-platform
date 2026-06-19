import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { GameCard } from '@/components/GameCard';
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
        <p className="eyebrow blue">{sport.label} slate board</p>
        <h2>{sport.title}</h2>
        <p className="hero-copy">{sport.description}</p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/parlays/build" style={{ textDecoration: 'none' }}>Build from this sport</Link>
          <Link className="ghost-button large" href="/sports" style={{ textDecoration: 'none' }}>Back to sports</Link>
        </div>
      </section>

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
              <p>Zero forced structure. If a safe parlay cannot be built, the app refuses instead of increasing risk.</p>
            </article>
            <article className="rank-card">
              <div className="rank-head"><span>Containment</span><b>TOP-3</b></div>
              <h4>{rankings[0]?.structure ?? 'MIXED 2-SOLID-1-CF'}</h4>
              <p>{rankings[0]?.note ?? 'Ranked output is built around containment, not pick-selling.'}</p>
            </article>
          </div>
        </aside>
      </section>
    </main>
  );
}
