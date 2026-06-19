import Link from 'next/link';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';
import { GameCard } from '@/components/GameCard';
import { RankingCard } from '@/components/RankingCard';
import { LineMovementGraph } from '@/components/LineMovementGraph';
import { PaidPreviewGate } from '@/components/PaidPreviewGate';
import { ContentBlock } from '@/components/ContentBlock';
import { sports } from '@/lib/sports';

export default async function Home() {
  const { games, rankings, statusCards, lineMovement, apiStatus, apiDetail } = await getApiSnapshot();

  return (
    <main className="shell">
      <AppHeader apiStatus={apiStatus} apiDetail={apiDetail} />

      <section className="hero-grid">
        <div className="hero-card glass-card">
          <p className="eyebrow blue">First week free · built for sharper sports research</p>
          <h2>Before you build a parlay, see how the market is actually moving.</h2>
          <p className="hero-copy">
            Silvers Syndicate is for people who want more than a gut pick. We track line movement, market pressure,
            steam, resistance, and timing so you can see where the board looks clean, where it looks shaky, and when
            the smartest move may be to pass. Start with a free week and see how the board reads before monthly access begins.
          </p>
          <div className="hero-actions">
            <Link className="primary-button large" href="/register?promo=free-week" style={{ textDecoration: 'none' }}>Start Free Week</Link>
            <Link className="ghost-button large" href="/picks-audit" style={{ textDecoration: 'none' }}>Test Your Picks</Link>
            <Link className="ghost-button large" href="/sports" style={{ textDecoration: 'none' }}>Preview the Board</Link>
            <Link className="ghost-button large" href="/methodology" style={{ textDecoration: 'none' }}>How It Works</Link>
          </div>
        </div>

        <aside className="bet-slip glass-card">
          <div className="slip-head">
            <span>Preview Slip</span>
            <strong>Locked</strong>
          </div>
          {rankings[0].legs.slice(0, 2).map((leg) => (
            <div className="slip-leg" key={leg}>
              <span>{leg}</span>
              <b>Preview</b>
            </div>
          ))}
          <div className="slip-leg">
            <span>Premium leg hidden</span>
            <b>Members only</b>
          </div>
          <div className="slip-total">
            <span>Rank #1</span>
            <strong>LOCKED</strong>
          </div>
          <p className="slip-note">Create an account to unlock full rankings, signal notes, coin-flip markers, and reason-coded no-build alerts.</p>
        </aside>
      </section>

      <section className="status-row">
        {statusCards.map((card) => (
          <article className="status-card" key={card.label}>
            <span>{card.label}</span>
            <strong>{card.value}</strong>
            <p>{card.detail}</p>
          </article>
        ))}
      </section>

      <ContentBlock
        eyebrow="Why people use it"
        title="A cleaner way to read the board"
        body="Most sports sites show you the line. Silvers Syndicate helps explain the story behind the line: when the move started, whether books agreed, where resistance showed up, and which games are too messy to force. It is sports market intelligence in plain English, built for people who want structure before they make decisions."
        items={[
          { title: 'Follow the timeline', detail: 'See how the market changes from early snapshot to later confirmation windows.' },
          { title: 'Read the pressure', detail: 'Steam, resistance, reversal, trap, chaos, and anomaly labels make movement easier to understand.' },
          { title: 'Separate strong from shaky', detail: 'Anchor legs, coin-flip variables, and no-overlap builds are kept in their own lanes.' },
          { title: 'Try it first', detail: 'Your first week is free, so you can see the workflow before committing.' }
        ]}
      />

      <section className="panel" style={{ marginBottom: 20, marginTop: 20 }}>
        <div className="panel-header">
          <div>
            <p className="eyebrow">Pick audit</p>
            <h3>Want the tougher version?</h3>
          </div>
          <Link className="ghost-button" href="/picks-audit" style={{ textDecoration: 'none' }}>See Why Picks Fail</Link>
        </div>
        <p className="hero-copy" style={{ marginTop: 8 }}>
          The regular board shows you how the market is moving. The pick audit page takes the opposite angle: it looks for
          the weak leg, the bad movement, and the reason your ticket may not hold up.
        </p>
      </section>

      <section className="panel" style={{ marginBottom: 20, marginTop: 20 }}>
        <div className="panel-header">
          <div>
            <p className="eyebrow">Sports Coverage</p>
            <h3>Every sport has its own board</h3>
          </div>
          <Link className="ghost-button" href="/sports" style={{ textDecoration: 'none' }}>All Sports</Link>
        </div>
        <div className="league-tabs">
          {sports.map((sport) => (
            <Link className="ghost-button" href={`/sports/${sport.slug}`} key={sport.slug} style={{ textDecoration: 'none' }}>{sport.label}</Link>
          ))}
        </div>
      </section>

      <PaidPreviewGate title="Unlock the full market board">
        <section className="content-grid">
          <div className="panel slate-panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Today’s Board</p>
                <h3>Eligible games</h3>
              </div>
              <div className="league-tabs">
                {sports.slice(0, 4).map((sport, index) => (
                  <Link className={index === 0 ? 'active' : ''} href={`/sports/${sport.slug}`} key={sport.slug} style={{ textDecoration: 'none' }}>{sport.label}</Link>
                ))}
              </div>
            </div>

            <div className="game-list">
              {games.map((game) => <GameCard game={game} key={game.id} />)}
            </div>
          </div>

          <aside className="panel rank-panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">8-Combo Ranking</p>
                <h3>Containment zone</h3>
              </div>
            </div>
            <div className="rank-list">
              {rankings.map((ranking) => <RankingCard ranking={ranking} key={ranking.rank} />)}
            </div>
          </aside>
        </section>

        <LineMovementGraph data={lineMovement} />
      </PaidPreviewGate>
    </main>
  );
}
