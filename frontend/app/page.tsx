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
          <p className="eyebrow blue">First week free · sports market intelligence</p>
          <h2>See the market move before you build the slate.</h2>
          <p className="hero-copy">
            Silvers Syndicate is a premium sports research terminal built around line movement, T-snapshots, steam, resistance,
            market anomalies, coin-flip risk, and ranked parlay structure. New members can start with a free first week and preview
            the system before monthly membership begins.
          </p>
          <div className="hero-actions">
            <Link className="primary-button large" href="/register?promo=free-week" style={{ textDecoration: 'none' }}>Start Free Week</Link>
            <Link className="ghost-button large" href="/sports" style={{ textDecoration: 'none' }}>Preview Sports Lobby</Link>
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
          <p className="slip-note">Register to unlock full Top-8 rankings, true coin-flip markers, signal detail, and reason-coded refusals.</p>
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
        eyebrow="Why members use it"
        title="A research-first board for serious sports fans"
        body="Most sports pages show scores, headlines, or isolated prices. Silvers Syndicate organizes the market itself: when each snapshot was captured, which books agreed, where resistance appeared, where a favorite strengthened, and when the safest answer is no build at all. The product is designed for people searching for sports market intelligence, parlay risk research, line movement tools, and a disciplined alternative to pick-selling content."
        items={[
          { title: 'T-snapshot timeline', detail: 'Track T1, T2, T3, T4, and safety-only T5 logic in one place.' },
          { title: 'Signal vocabulary', detail: 'Steam, resistance, reversal, trap, chaos, and market anomaly labels explain what changed.' },
          { title: 'Parlay structure', detail: 'See how anchor legs, coin-flip variables, and no-overlap builds are separated.' },
          { title: 'Free week', detail: 'New members can explore the terminal for the first week before monthly access begins.' }
        ]}
      />

      <section className="panel" style={{ marginBottom: 20, marginTop: 20 }}>
        <div className="panel-header">
          <div>
            <p className="eyebrow">Sports Coverage</p>
            <h3>Pages built for every major board and niche market</h3>
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
