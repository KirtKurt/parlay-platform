import Link from 'next/link';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { ContentBlock } from '@/components/ContentBlock';
import { SportEquipmentIcon, SportIconStrip, sportVisuals } from '@/components/SportVisuals';
import { getSportSlugForLeague, sports } from '@/lib/sports';

export default async function SportsPage() {
  const { games, apiStatus, apiDetail } = await getApiSnapshot();

  return (
    <main className="shell">
      <AppHeader title="Sports lobby" apiStatus={apiStatus} apiDetail={apiDetail} />

      <section className="sport-hero-grid">
        <div className="hero-card glass-card" style={{ minHeight: 0 }}>
          <p className="eyebrow blue">All sports · first week free</p>
          <h2>Pick your sport. We’ll organize the board.</h2>
          <p className="hero-copy">
            Start with the sport you care about today. Each board uses the same visual language: equipment icons for the sport,
            jersey-style badges for teams, and clean signal labels for market pressure.
          </p>
          <div className="hero-actions">
            <Link className="primary-button large" href="/register?promo=free-week" style={{ textDecoration: 'none' }}>Start Free Week</Link>
            <Link className="ghost-button large" href="/picks-audit" style={{ textDecoration: 'none' }}>Test Your Picks</Link>
            <Link className="ghost-button large" href="/methodology" style={{ textDecoration: 'none' }}>Read Methodology</Link>
          </div>
        </div>
        <aside className="sport-hero-panel accent-blue">
          <SportEquipmentIcon slug="nfl" size="large" showLabel />
          <h3>Equipment-first navigation</h3>
          <p>Every sport gets a familiar ball, helmet, puck, stick, racket, board, or paddle so the board feels faster to read.</p>
          <div className="mini-equipment-line"><span>Custom icons</span><span>Team badges</span><span>Clear labels</span></div>
        </aside>
      </section>

      <SportIconStrip />

      <section className="visual-route-strip">
        {sports.map((sport) => {
          const count = games.filter((game) => getSportSlugForLeague(game.league) === sport.slug).length;
          const visual = sportVisuals[sport.slug];
          return (
            <Link className="visual-route-card" href={`/sports/${sport.slug}`} key={sport.slug} style={{ textDecoration: 'none', color: 'inherit' }}>
              <SportEquipmentIcon slug={sport.slug} />
              <span className="eyebrow blue">{visual.equipmentLabel}</span>
              <strong>{sport.title}</strong>
              <p>{count ? `${count} active example on the board.` : 'Ready for slate data.'} {visual.description}</p>
            </Link>
          );
        })}
      </section>

      <ContentBlock
        eyebrow="Best-practice structure"
        title="The sport page should be obvious before anyone reads a paragraph."
        body="A visitor should know where they are in one second. The site now uses equipment icons for each sport, jersey-style badges for team identity, and the same signal language across every market board."
        items={[
          { title: 'Sport identity', detail: 'Football, basketball, puck, bat, racket, soccer ball, dartboard, lacrosse stick, and paddle graphics make the sport visible instantly.' },
          { title: 'Team identity', detail: 'Team names and abbreviations sit inside custom jersey-style badges.' },
          { title: 'Signal identity', detail: 'Steam, resistance, coin flip, anomaly, and no-overlap structure stay consistent across every route.' },
          { title: 'Search-friendly pages', detail: 'Every board keeps natural text around the visual system so users, search engines, and AI tools understand the coverage.' }
        ]}
      />
    </main>
  );
}
