import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { GameCard } from '@/components/GameCard';
import { PaidPreviewGate } from '@/components/PaidPreviewGate';
import { ContentBlock } from '@/components/ContentBlock';
import { SportEquipmentIcon, SportHeroPanel, SportIconStrip } from '@/components/SportVisuals';
import { getSportBySlug, getSportSlugForLeague, sports } from '@/lib/sports';

export function generateStaticParams() {
  return sports.map((sport) => ({ sport: sport.slug }));
}

export default async function SportPage({ params }: { params: { sport: string } }) {
  const sport = getSportBySlug(params.sport);
  if (!sport) notFound();

  const { games, rankings, apiStatus, apiDetail } = await getApiSnapshot();
  const visibleGames = games.filter((game) => getSportSlugForLeague(game.league) === sport.slug);
  const hasMarketData = visibleGames.length > 0;

  return (
    <main className="shell">
      <AppHeader title={sport.title} apiStatus={apiStatus} apiDetail={apiDetail} />

      <section className="sport-hero-grid">
        <div className="hero-card glass-card" style={{ minHeight: 0 }}>
          <p className="eyebrow blue">{sport.label} board · 5 days free</p>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, margin: '8px 0 10px' }}>
            <SportEquipmentIcon slug={sport.slug} size="large" />
            <h2 style={{ margin: 0 }}>{sport.title}</h2>
          </div>
          <p className="hero-copy">
            {hasMarketData
              ? 'Market data is available for this board. Review the slate, scan the signals, and check where the risk is showing up before lock-in.'
              : 'Waiting for market data. Once verified market data is available, InQsi will show game leans, market signals, and risk checks for this board.'}
          </p>
          <div className="hero-actions">
            <Link className="primary-button large" href={`/register?promo=5-days&sport=${sport.slug}`} style={{ textDecoration: 'none' }}>Unlock {sport.label}</Link>
            <Link className="ghost-button large" href="/sports" style={{ textDecoration: 'none' }}>Back to sports</Link>
            <Link className="ghost-button large" href="/login" style={{ textDecoration: 'none' }}>Log In</Link>
          </div>
        </div>
        <SportHeroPanel sportSlug={sport.slug} title="Check the board before you trust the pick." copy="InQsi keeps the review focused on the signals that matter: support, resistance, volatility, and the warning signs that could change how you feel about a slip." />
      </section>

      <SportIconStrip compact />

      <section className="status-row">
        <article className="status-card"><SportEquipmentIcon slug={sport.slug} /><span>Market Data</span><strong>{hasMarketData ? `${visibleGames.length} Active` : 'Waiting'}</strong><p>{hasMarketData ? `This ${sport.label} board has verified market data available.` : 'Waiting for market data.'}</p></article>
        <article className="status-card"><span>Signals</span><strong>{hasMarketData ? 'Available' : 'Waiting'}</strong><p>{hasMarketData ? 'Signal detail is ready for review.' : 'Signals appear after market data is available.'}</p></article>
        <article className="status-card"><span>Movement</span><strong>{hasMarketData ? 'Ready' : 'Waiting'}</strong><p>{hasMarketData ? 'Movement detail unlocks after registration.' : 'Line movement will appear with verified market data.'}</p></article>
        <article className="status-card"><span>Promo</span><strong>5 days</strong><p>New members can start with 5 days free.</p></article>
      </section>

      <ContentBlock
        eyebrow={`${sport.label} guide`}
        title={`A simpler way to read the ${sport.label} board`}
        body={`The ${sport.label} board is built to show more than a final price. It helps you see whether a matchup is getting support, running into resistance, or becoming too unstable to force.`}
        items={[
          { title: 'Start with the slate', detail: `Open the ${sport.label} board and see which games are ready for review.` },
          { title: 'Look for pressure', detail: 'Support, resistance, and unusual movement help show where the market may be warning you.' },
          { title: 'Keep the risk readable', detail: 'Steam, resistance, coin-flip, chaos, and anomaly labels stay consistent across sports.' },
          { title: 'Unlock the full view', detail: 'Public preview stays simple. Full ranking and movement detail unlock after registration.' }
        ]}
      />

      <PaidPreviewGate title={`Unlock the full ${sport.label} board`}>
        <section className="content-grid">
          <div className="panel slate-panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Market Board</p>
                <h3>{hasMarketData ? `${visibleGames.length} active game${visibleGames.length === 1 ? '' : 's'}` : 'Waiting for market data'}</h3>
              </div>
              <SportEquipmentIcon slug={sport.slug} />
            </div>
            <div className="game-list">
              {hasMarketData ? visibleGames.map((game) => <GameCard game={game} key={game.id} />) : (
                <article className="game-card">
                  <h4>Waiting for market data</h4>
                  <p className="movement">Once verified market data is available, InQsi will show game leans, market signals, and risk checks for this board.</p>
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
                <p>No forced structure. If the board does not support a safe build, InQsi should slow you down instead of dressing it up.</p>
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
