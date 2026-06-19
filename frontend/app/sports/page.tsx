import Link from 'next/link';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { getSportSlugForLeague, sports } from '@/lib/sports';

export default async function SportsPage() {
  const { games, apiStatus, apiDetail } = await getApiSnapshot();

  return (
    <main className="shell">
      <AppHeader title="Sports lobby" apiStatus={apiStatus} apiDetail={apiDetail} />

      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Multi-sport terminal</p>
        <h2>One template scales to every sport and every slate.</h2>
        <p className="hero-copy">
          Each sport page pulls the same backend game structure and filters by league. We do not create 100 static pages — the app creates game and match pages from data.
        </p>
      </section>

      <section className="status-row">
        {sports.slice(0, 4).map((sport) => {
          const count = games.filter((game) => getSportSlugForLeague(game.league) === sport.slug).length;
          return (
            <Link className="status-card" href={`/sports/${sport.slug}`} key={sport.slug} style={{ textDecoration: 'none', color: 'inherit' }}>
              <span>{sport.label}</span>
              <strong>{count || 'Ready'}</strong>
              <p>{sport.description}</p>
            </Link>
          );
        })}
      </section>

      <section className="status-row">
        {sports.slice(4, 8).map((sport) => {
          const count = games.filter((game) => getSportSlugForLeague(game.league) === sport.slug).length;
          return (
            <Link className="status-card" href={`/sports/${sport.slug}`} key={sport.slug} style={{ textDecoration: 'none', color: 'inherit' }}>
              <span>{sport.label}</span>
              <strong>{count || 'Ready'}</strong>
              <p>{sport.description}</p>
            </Link>
          );
        })}
      </section>

      <section className="status-row">
        {sports.slice(8).map((sport) => {
          const count = games.filter((game) => getSportSlugForLeague(game.league) === sport.slug).length;
          return (
            <Link className="status-card" href={`/sports/${sport.slug}`} key={sport.slug} style={{ textDecoration: 'none', color: 'inherit' }}>
              <span>{sport.label}</span>
              <strong>{count || 'Ready'}</strong>
              <p>{sport.description}</p>
            </Link>
          );
        })}
      </section>
    </main>
  );
}
