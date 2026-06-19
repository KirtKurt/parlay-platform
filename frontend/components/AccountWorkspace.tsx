'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { clearMemberSession, getMemberSession, MemberSession } from '@/lib/memberSession';

function formatDate(value?: string) {
  if (!value) return 'Pending live billing';
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(value));
}

export function AccountWorkspace() {
  const [session, setSession] = useState<MemberSession | null>(null);

  useEffect(() => {
    setSession(getMemberSession());
  }, []);

  if (!session) {
    return (
      <>
        <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
          <p className="eyebrow blue">Member workspace</p>
          <h2>You are not signed in yet.</h2>
          <p className="hero-copy">Log in to open your market board, saved sports, and free-week access.</p>
          <div className="hero-actions">
            <Link className="primary-button large" href="/login" style={{ textDecoration: 'none' }}>Login</Link>
            <Link className="ghost-button large" href="/register" style={{ textDecoration: 'none' }}>Create account</Link>
          </div>
        </section>
      </>
    );
  }

  return (
    <>
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Member workspace</p>
        <h2>Welcome back.</h2>
        <p className="hero-copy">You are signed in as {session.email}. Open the sports board, test a pick, or review your plan access.</p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/sports" style={{ textDecoration: 'none' }}>Open Sports Board</Link>
          <Link className="ghost-button large" href="/picks-audit" style={{ textDecoration: 'none' }}>Test Your Picks</Link>
          <button
            className="ghost-button large"
            type="button"
            onClick={() => {
              clearMemberSession();
              window.location.href = '/';
            }}
          >
            Log out
          </button>
        </div>
      </section>
      <section className="status-row">
        <article className="status-card"><span>Status</span><strong>Signed in</strong><p>Frontend member session is active for testing.</p></article>
        <article className="status-card"><span>Plan</span><strong>{session.plan}</strong><p>{session.plan === 'Pro' ? 'Advanced market and build tools.' : 'Core market board access.'}</p></article>
        <article className="status-card"><span>Free Week Ends</span><strong>{formatDate(session.freeWeekEndsAt)}</strong><p>The live billing provider will enforce the real renewal date.</p></article>
        <article className="status-card"><span>Saved Sports</span><strong>11</strong><p>NFL, CFB, NBA, NCAAM, NHL, MLB, Tennis, Soccer, Darts, Lacrosse, Table Tennis.</p></article>
      </section>
      <section className="content-grid" style={{ marginTop: 20 }}>
        <article className="panel">
          <div className="panel-header compact">
            <div>
              <p className="eyebrow blue">Next best actions</p>
              <h3>Where to go from here</h3>
            </div>
          </div>
          <div className="game-list">
            <Link className="game-card" href="/sports" style={{ color: 'inherit', textDecoration: 'none' }}>
              <div className="game-topline"><span className="league-chip">Board</span><span>Live preview</span></div>
              <h4>Open the market board</h4>
              <p className="movement">Browse the sports slate, line movement, and signal tags.</p>
            </Link>
            <Link className="game-card" href="/picks-audit" style={{ color: 'inherit', textDecoration: 'none' }}>
              <div className="game-topline"><span className="league-chip">Audit</span><span>Risk check</span></div>
              <h4>Test a pick before you trust it</h4>
              <p className="movement">Use the negative-check workflow to see where a pick may break down.</p>
            </Link>
          </div>
        </article>
        <aside className="panel">
          <p className="eyebrow blue">Security note</p>
          <h3>Live login comes next</h3>
          <p className="movement">This login works for product testing and navigation. Before real paid subscribers, account access should move to AWS Cognito and subscription status should be checked by the backend.</p>
        </aside>
      </section>
    </>
  );
}
