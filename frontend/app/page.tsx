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
          <p className="eyebrow blue">First week free · check the pick before the ticket</p>
          <h2>Before you trust the pick, see what the market is saying.</h2>
          <p className="hero-copy">
            Silvers Syndicate helps you slow down before a parlay gets expensive. Bring the pick you already like,
            check the movement behind it, look for weak-leg risk, and decide whether the board is clean enough to keep moving.
          </p>
          <div className="hero-actions">
            <Link className="primary-button large" href="/picks-audit" style={{ textDecoration: 'none' }}>Test Your Picks</Link>
            <Link className="ghost-button large" href="/start-here" style={{ textDecoration: 'none' }}>Start Here</Link>
            <Link className="ghost-button large" href="/sports" style={{ textDecoration: 'none' }}>Preview the Board</Link>
            <Link className="ghost-button large" href="/register?promo=free-week" style={{ textDecoration: 'none' }}>Start Free Week</Link>
          </div>
        </div>

        <aside className="bet-slip glass-card">
          <div className="slip-head">
            <span>Market Check</span>
            <strong>Preview</strong>
          </div>
          {rankings[0].legs.slice(0, 2).map((leg) => (
            <div className="slip-leg" key={leg}>
              <span>{leg}</span>
              <b>Check</b>
            </div>
          ))}
          <div className="slip-leg">
            <span>Weak-leg report</span>
            <b>Locked</b>
          </div>
          <div className="slip-total">
            <span>Full ranking</span>
            <strong>MEMBERS</strong>
          </div>
          <p className="slip-note">Start free to unlock full rankings, signal notes, coin-flip markers, and reason-coded no-build alerts.</p>
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
        eyebrow="Choose your path"
        title="Three ways to use the site"
        body="The website is now organized around what visitors are usually trying to do first: challenge a pick, browse a sport, or understand what makes the system different."
        items={[
          { title: 'I already have a pick', detail: 'Go to the pick audit and look for the market reasons it may fail before you lock it in.' },
          { title: 'I want to browse today’s board', detail: 'Choose a sport and review the slate structure, signal tags, and premium preview.' },
          { title: 'I want to know how this works', detail: 'Read the methodology, then compare Core vs Pro and start the free week when you are ready.' },
          { title: 'I want the full output', detail: 'Register for the free week to unlock the full board, rankings, and market notes.' }
        ]}
      />

      <section className="panel" style={{ marginBottom: 20, marginTop: 20 }}>
        <div className="panel-header">
          <div>
            <p className="eyebrow">Pick audit</p>
            <h3>Start with the thing most people skip: why it might lose.</h3>
          </div>
          <Link className="ghost-button" href="/picks-audit" style={{ textDecoration: 'none' }}>See Why Picks Fail</Link>
        </div>
        <p className="hero-copy" style={{ marginTop: 8 }}>
          The regular board shows you how the market is moving. The pick audit page takes the tougher angle: it looks for
          the weak leg, bad movement, and the reason your ticket may not hold up.
        </p>
      </section>

      <section className="panel" style={{ marginBottom: 20, marginTop: 20 }}>
        <div className="panel-header">
          <div>
            <p className="eyebrow">Sports Coverage</p>
            <h3>Every sport gets its own board</h3>
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
