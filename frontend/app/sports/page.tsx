import Link from 'next/link';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { ContentBlock } from '@/components/ContentBlock';
import { getSportSlugForLeague, sports } from '@/lib/sports';

export default async function SportsPage() {
  const { games, apiStatus, apiDetail } = await getApiSnapshot();

  return (
    <main className="shell">
      <AppHeader title="Sports lobby" apiStatus={apiStatus} apiDetail={apiDetail} />

      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">All sports · first week free</p>
        <h2>Pick your sport. We’ll organize the board.</h2>
        <p className="hero-copy">
          Jump into NFL, college football, NBA, college hoops, NHL, MLB, tennis, soccer, darts, lacrosse, or table tennis.
          Each sport has its own board so you can quickly see what is live, what is moving, and which matchups deserve a closer look.
        </p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/register?promo=free-week" style={{ textDecoration: 'none' }}>Start Free Week</Link>
          <Link className="ghost-button large" href="/methodology" style={{ textDecoration: 'none' }}>Read Methodology</Link>
        </div>
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

      <ContentBlock
        eyebrow="What you get from each board"
        title="Less scrolling. More context."
        body="The goal is simple: make every sport easier to read. Instead of jumping between odds screens, headlines, and random opinions, each board gives you one place to check line movement, market signals, available games, and the premium research layer when you are signed in."
        items={[
          { title: 'Sport-specific pages', detail: 'Each sport has a clean URL and its own board, which helps users and search engines understand the coverage.' },
          { title: 'Game pages ready to grow', detail: 'As more data comes in, each matchup can become a deeper timeline with notes, movement, and signal history.' },
          { title: 'Consistent language', detail: 'Steam, resistance, chaos, anomaly, and T-snapshot language stays the same across sports.' },
          { title: 'Preview before you join', detail: 'You can see the structure first. Full movement and rankings unlock for members.' }
        ]}
      />
    </main>
  );
}
