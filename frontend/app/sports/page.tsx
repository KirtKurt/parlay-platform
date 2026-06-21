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
          <p className="eyebrow blue">All sports · 5 days free</p>
          <h2>Pick your sport. We’ll organize the board.</h2>
          <p className="hero-copy">
            Start with the sport you care about today. InQsi helps you see where the market is showing support, pressure, and warning signs before you lock in a pick.
          </p>
          <div className="hero-actions">
            <Link className="primary-button large" href="/register?promo=5-days" style={{ textDecoration: 'none' }}>Start 5 Days Free</Link>
            <Link className="ghost-button large" href="/picks-audit" style={{ textDecoration: 'none' }}>Test Your Picks</Link>
            <Link className="ghost-button large" href="/methodology" style={{ textDecoration: 'none' }}>Read Methodology</Link>
          </div>
        </div>
        <aside className="sport-hero-panel accent-blue">
          <SportEquipmentIcon slug="nfl" size="large" showLabel />
          <h3>Find the board faster</h3>
          <p>Choose your sport, scan the slate, and look for the signals that could make a pick stronger, weaker, or too risky to force.</p>
          <div className="mini-equipment-line"><span>Sports</span><span>Signals</span><span>Risk checks</span></div>
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
              <p>{count ? `${count} active example on the board.` : 'Ready for slate data.'} Open the board when you want to review market pressure and risk signals for this sport.</p>
            </Link>
          );
        })}
      </section>

      <ContentBlock
        eyebrow="How to use the board"
        title="Start with the sport. Then check the risk."
        body="You should not have to dig through complicated screens to understand what matters. Pick the sport, review the slate, scan the signals, and look for the places where your ticket may be weaker than it feels."
        items={[
          { title: 'Choose the sport', detail: 'Open the board that matches what you are watching today.' },
          { title: 'Scan the slate', detail: 'See which games are showing support, resistance, coin-flip pressure, or unusual movement.' },
          { title: 'Check the weak spots', detail: 'Use the same signal language across every sport so the warning signs stay easy to read.' },
          { title: 'Avoid forced confidence', detail: 'If the board is messy, InQsi should slow you down instead of dressing up a risky answer.' }
        ]}
      />
    </main>
  );
}
