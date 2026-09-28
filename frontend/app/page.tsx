import type { Metadata } from 'next';
import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { getApiSnapshot } from '@/lib/api';
import { formatKickoff, gamePath } from '@/lib/kickoff';
import { sports as sportNav } from '@/lib/sports';

export const dynamic = 'force-dynamic';

export const metadata: Metadata = {
  title: 'ARB, Official 3-Legs, Game Leans & Slip Scanner',
  description: 'InQsi tools for live arbitrage, official 3-leg parlays with breakdowns, per-game predicted winners, and AI slip scanning.',
  alternates: { canonical: '/' }
};

function parlayLegs(row: any): string[] {
  if (Array.isArray(row?.legs)) return row.legs.map((leg: any) => typeof leg === 'string' ? leg : String(leg?.team || leg?.pick || leg?.name || '')).filter(Boolean);
  if (Array.isArray(row?.picks)) return row.picks.map((pick: any) => String(pick?.team || pick || '')).filter(Boolean);
  return String(row?.structure || row?.title || '').split(/\s*[x×,]\s*/).map((part) => part.trim()).filter(Boolean).slice(0, 3);
}

export default async function Home() {
  const { games, predictions, rankings, apiStatus, apiDetail } = await getApiSnapshot();
  const official = (Array.isArray(rankings) ? rankings : []).slice(0, 2);
  const leans = games.filter((game) => game.predicted_winner || game.predicted_side).slice(0, 8);
  const board = (leans.length ? leans : games).slice(0, 8);

  return (
    <main className="inqsi-shell tool-shell">
      <AppHeader eyebrow="InQsi" title="Tool Workspace" apiStatus={apiStatus} apiDetail={apiDetail} />

      <nav className="tool-tabs" aria-label="InQsi tools">
        <Link className="tool-tab on" href="/">Games</Link>
        <Link className="tool-tab" href="/arbitrage-v2">ARB</Link>
        <Link className="tool-tab" href="/parlays">3-Leg</Link>
        <Link className="tool-tab" href="/game-leans">Leans</Link>
        <Link className="tool-tab" href="/parlay-scanner">Scan</Link>
      </nav>

      <nav className="inqsi-tabs" aria-label="Sports">
        <Link href="/">All</Link>
        {sportNav.map((sport) => <Link key={sport.slug} href={`/sports/${sport.slug}`}>{sport.label}</Link>)}
      </nav>

      <section className="tool-kpis">
        <Link href="/arbitrage-v2"><b>ARB</b><span>Live books and stake math</span></Link>
        <Link href="/parlays"><b>{official.length || 'Waiting'}</b><span>Official 3-leg slips</span></Link>
        <Link href="/game-leans"><b>{predictions.length || leans.length || 'Waiting'}</b><span>Game predictions</span></Link>
        <Link href="/parlay-scanner"><b>Scan</b><span>Review a 3-leg slip</span></Link>
      </section>

      {official.length > 0 && (
        <section className="tool-feed">
          <div className="tool-feed-head">
            <h2>Official 3-leg</h2>
            <Link href="/parlays">Open breakdown</Link>
          </div>
          {official.map((row: any, index: number) => {
            const legs = parlayLegs(row);
            return (
              <article className="tool-row" key={row.id || index}>
                <div>
                  <small>3-LEG</small>
                  <strong>{row.structure || row.title || 'Official hourly structure'}</strong>
                  <p>{row.note || row.explanation || 'Published from the hourly builder. Combined odds stay hidden until a live price exists.'}</p>
                  {legs.length > 0 && (
                    <ol className="tool-legs">
                      {legs.slice(0, 3).map((leg) => <li key={leg}>{leg}</li>)}
                    </ol>
                  )}
                </div>
                <b className="tool-edge">{row.american || row.combined_odds || 'Waiting'}</b>
              </article>
            );
          })}
        </section>
      )}

      <section className="tool-feed" id="main-content">
        <div className="tool-feed-head">
          <h2>{leans.length ? 'Predicted winners' : 'Live games'}</h2>
          <Link href="/game-leans">All leans</Link>
        </div>
        {board.length ? board.map((game) => {
          const lean = game.predicted_winner || game.predicted_side;
          return (
            <Link className="tool-row" href={gamePath(game)} key={game.id}>
              <div>
                <small>{game.league || game.sport_key} · {formatKickoff(game.start || game.commence_time)}</small>
                <strong>{game.matchup}</strong>
                <p>
                  {lean
                    ? `InQsi lean: ${lean}${game.confidence ? ` · ${game.confidence}` : ''}`
                    : `Market favorite: ${game.favorite || 'Waiting'}. Waiting on a published prediction.`}
                </p>
              </div>
              <span className="tool-badges">
                {lean && <em>LEAN</em>}
                <em className="muted">ARB</em>
                <em className="muted">3-LEG</em>
              </span>
            </Link>
          );
        }) : (
          <article className="tool-row">
            <div>
              <small>SYNCING</small>
              <strong>Waiting on live tool data</strong>
              <p>InQsi does not invent winners or parlay prices while the board is empty.</p>
            </div>
          </article>
        )}
      </section>
    </main>
  );
}
