import Link from 'next/link';
import type { Metadata } from 'next';
import { AppHeader } from '@/components/AppHeader';
import { ContentBlock } from '@/components/ContentBlock';
import { PaidPreviewGate } from '@/components/PaidPreviewGate';
import { getApiSnapshot } from '@/lib/api';

export const metadata: Metadata = {
  title: 'Why Your Parlay Picks Fail | Silvers Syndicate Picks Audit',
  description:
    'Run your picks through market movement, steam, resistance, trap risk, and weak-leg exposure before you lock in a parlay.',
  alternates: {
    canonical: 'https://silverssyndicate.app/picks-audit'
  },
  openGraph: {
    title: 'See why your picks might fail before you play them',
    description:
      'Silvers Syndicate checks line movement, resistance, market pressure, and weak-leg exposure before you build a parlay.',
    url: 'https://silverssyndicate.app/picks-audit',
    siteName: 'Silvers Syndicate',
    type: 'website'
  },
  twitter: {
    card: 'summary_large_image',
    title: 'See why your picks might fail',
    description: 'A market-first picks audit for parlays, steam, resistance, and weak-leg risk.'
  }
};

const auditChecks = [
  {
    title: 'Market moving against you',
    detail: 'Your side may look obvious, but the price can tell a different story when multiple books start leaning the other way.'
  },
  {
    title: 'One leg carrying the risk',
    detail: 'A parlay can look clean on the surface while one unstable leg quietly creates most of the failure risk.'
  },
  {
    title: 'Favorite taking resistance',
    detail: 'Heavy favorite does not always mean clean favorite. Resistance, reversal, and late instability matter.'
  },
  {
    title: 'Too many coin-flip spots',
    detail: 'If the board is messy, forcing a build is usually the problem. The audit shows where the pass signal starts.'
  }
];

const comparisons = [
  {
    pick: '“This team should win.”',
    audit: '“The market is moving against that side.”'
  },
  {
    pick: '“This parlay feels safe.”',
    audit: '“One leg is doing most of the damage.”'
  },
  {
    pick: '“The favorite is obvious.”',
    audit: '“The favorite is showing resistance across the board.”'
  },
  {
    pick: '“I just need three winners.”',
    audit: '“You also need structure, timing, and a reason not to force it.”'
  }
];

export default async function PicksAuditPage() {
  const { apiStatus, apiDetail } = await getApiSnapshot();

  return (
    <main className="shell">
      <AppHeader apiStatus={apiStatus} apiDetail={apiDetail} />

      <section className="hero-grid">
        <div className="hero-card glass-card">
          <p className="eyebrow red">First week free · picks audit</p>
          <h2>See why your picks might not work before you lock them in.</h2>
          <p className="hero-copy">
            Most parlays look good before the games start. The problem usually shows up in the market: a line that moves
            the wrong way, a favorite taking resistance, a total that will not settle, or one weak leg hiding inside a
            good-looking ticket. Silvers Syndicate is built to show you those problems first.
          </p>
          <div className="hero-actions">
            <Link className="primary-button large" href="/register?promo=free-week&source=picks-audit" style={{ textDecoration: 'none' }}>
              Run Your Picks
            </Link>
            <Link className="ghost-button large" href="/pricing" style={{ textDecoration: 'none' }}>
              First Week Free
            </Link>
            <Link className="ghost-button large" href="/" style={{ textDecoration: 'none' }}>
              Main Home Page
            </Link>
          </div>
        </div>

        <aside className="bet-slip glass-card">
          <div className="slip-head">
            <span>Pick Audit</span>
            <strong>Fail Check</strong>
          </div>
          <div className="slip-leg">
            <span>Your favorite leg</span>
            <b>Resistance?</b>
          </div>
          <div className="slip-leg">
            <span>Your coin-flip leg</span>
            <b>Exposed?</b>
          </div>
          <div className="slip-leg">
            <span>Your anchor leg</span>
            <b>Confirmed?</b>
          </div>
          <div className="slip-total">
            <span>Audit Result</span>
            <strong>LOCKED</strong>
          </div>
          <p className="slip-note">
            Create an account to unlock the full audit, weak-leg notes, market pressure labels, and reason-coded warnings.
          </p>
        </aside>
      </section>

      <section className="status-row">
        <article className="status-card">
          <span>What most people check</span>
          <strong>The pick</strong>
          <p>Team name, matchup, odds, and the feeling that the angle makes sense.</p>
        </article>
        <article className="status-card">
          <span>What we check</span>
          <strong>The pressure</strong>
          <p>Line movement, steam, resistance, reversals, timing, and book disagreement.</p>
        </article>
        <article className="status-card">
          <span>The goal</span>
          <strong>Find the weak leg</strong>
          <p>Not every bad build looks bad. The audit is designed to expose the risk before the ticket is built.</p>
        </article>
      </section>

      <ContentBlock
        eyebrow="The challenge"
        title="The market does not care how good your pick feels."
        body="A good story is not the same thing as a clean market. Silvers Syndicate looks for the places where your pick is being pushed, resisted, or contradicted by the board. Sometimes the answer is confidence. Sometimes the answer is caution. Sometimes the answer is do not force it."
        items={auditChecks}
      />

      <section className="panel" style={{ marginTop: 20, marginBottom: 20 }}>
        <div className="panel-header">
          <div>
            <p className="eyebrow">Your pick vs. market check</p>
            <h3>Same ticket. Two very different reads.</h3>
          </div>
          <Link className="ghost-button" href="/register?promo=free-week&source=picks-audit-compare" style={{ textDecoration: 'none' }}>
            Start Free Week
          </Link>
        </div>
        <div className="content-grid">
          {comparisons.map((row) => (
            <article className="status-card" key={row.pick}>
              <span>Your pick says</span>
              <strong>{row.pick}</strong>
              <p><b>Market check says:</b> {row.audit}</p>
            </article>
          ))}
        </div>
      </section>

      <PaidPreviewGate title="Unlock the full pick audit">
        <section className="content-grid">
          <article className="panel">
            <p className="eyebrow">Locked report</p>
            <h3>Weak-leg exposure</h3>
            <p className="hero-copy">
              See which leg is most likely to break the build, where the market pushed back, and whether the structure is
              strong enough to keep or too fragile to force.
            </p>
          </article>
          <article className="panel">
            <p className="eyebrow">Locked report</p>
            <h3>Market pressure notes</h3>
            <p className="hero-copy">
              Review steam, resistance, reversal, trap risk, chaos, and anomaly labels in plain English before you make a decision.
            </p>
          </article>
        </section>
      </PaidPreviewGate>

      <section className="panel" style={{ marginTop: 20 }}>
        <div className="panel-header">
          <div>
            <p className="eyebrow blue">Free week</p>
            <h3>Run the audit before the ticket.</h3>
          </div>
          <Link className="primary-button" href="/register?promo=free-week&source=picks-audit-bottom" style={{ textDecoration: 'none' }}>
            Start Free Week
          </Link>
        </div>
        <p className="hero-copy" style={{ marginTop: 8 }}>
          Use the first week to compare your own picks against the market read. Keep what holds up. Question what does not.
          Walk away from builds that only looked good before the data came in.
        </p>
      </section>
    </main>
  );
}
