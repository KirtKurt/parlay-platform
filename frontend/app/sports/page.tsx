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
        <p className="eyebrow blue">Multi-sport terminal · first week free</p>
        <h2>Every sport gets its own market board, game page, and signal vocabulary.</h2>
        <p className="hero-copy">
          Browse NFL, college football, NBA, NCAAM, NHL, MLB, tennis, soccer, darts, lacrosse, and table tennis from one sports intelligence lobby. Each page is built to rank high-value research terms naturally: line movement, steam, resistance, market anomaly, T-snapshot, game detail, and parlay structure.
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
        eyebrow="Search-friendly guide"
        title="Built for fans searching by sport, signal, and market behavior"
        body="The sports lobby gives search engines and AI assistants a clean map of the product: every supported sport has a dedicated URL, every game can become its own page, and the same language appears consistently across the site. That helps people discover Silvers Syndicate when they search for sports market intelligence, line movement analysis, parlay research tools, and signal-based slate monitoring."
        items={[
          { title: 'Dedicated sport URLs', detail: 'Each sport has its own page so search engines can understand coverage clearly.' },
          { title: 'Expandable game pages', detail: 'Game and match pages are generated from data instead of being manually built.' },
          { title: 'Consistent terms', detail: 'Steam, resistance, chaos, anomaly, and T-snapshot language repeats across pages.' },
          { title: 'Free preview', detail: 'Visitors can see enough value to understand the product before registration.' }
        ]}
      />
    </main>
  );
}
