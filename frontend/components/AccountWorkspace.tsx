'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { clearMemberSession, getMemberSession, MemberSession } from '@/lib/memberSession';
import { TeamJerseyBadge } from '@/components/SportVisuals';

function formatDate(value?: string) {
  if (!value) return 'Pending live billing';
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(value));
}

const workspaceLinks = [
  { href: '/parlay-scanner', chip: 'Scan', title: 'AI Slip Scanner', copy: 'Bring your own slip and check where the risk may be hiding.' },
  { href: '/parlays', chip: 'Build', title: 'Build My Slip', copy: 'Let InQsi help structure a cleaner slip from the games you choose.' },
  { href: '/sports/mlb', chip: 'Board', title: 'Sports Market Board', copy: 'Review active games, moneyline, spread, over/under, and market status.' },
  { href: '/account/slips', chip: 'Slips', title: 'My Slips & Scores', copy: 'Set slips public or private, review post-game analysis, and track accuracy over time.' },
  { href: '/game-leans', chip: 'Leans', title: 'Game Leans', copy: 'Review market-supported sides with risk context.' },
  { href: '/line-movement-review', chip: 'Movement', title: 'Line Movement Review', copy: 'See whether the number moved for you or against you after your first read.' },
  { href: '/performance', chip: 'History', title: 'Review History', copy: 'Look back at saved scans, builder outputs, and market reads.' },
  { href: '/watchlist', chip: 'Saved', title: 'Watchlist', copy: 'Keep the games and slips you care about close.' },
  { href: '/alerts', chip: 'Alerts', title: 'Alerts', copy: 'Review meaningful warning signs and market changes.' },
  { href: '/account/billing', chip: 'Billing', title: 'Billing & Payment Portal', copy: 'Update payment method, invoices, and subscription settings through the provider portal.' }
];

function WorkspaceLinkCard({ href, chip, title, copy }: { href: string; chip: string; title: string; copy: string }) {
  return (
    <Link className="game-card" href={href} style={{ color: 'inherit', textDecoration: 'none' }}>
      <div className="game-topline"><span className="league-chip">{chip}</span><span>Account menu</span></div>
      <h4>{title}</h4>
      <p className="movement">{copy}</p>
    </Link>
  );
}

function WelcomeFloat({ email, onClose }: { email?: string; onClose: () => void }) {
  return (
    <div
      role="dialog"
      aria-label="Welcome to InQsi"
      style={{
        position: 'fixed',
        left: '50%',
        bottom: 'max(22px, env(safe-area-inset-bottom))',
        transform: 'translateX(-50%)',
        zIndex: 50,
        width: 'min(720px, calc(100vw - 28px))',
        borderRadius: 24,
        border: '1px solid rgba(32, 242, 159, 0.32)',
        background: 'linear-gradient(135deg, rgba(7, 18, 34, 0.97), rgba(12, 26, 48, 0.97))',
        boxShadow: '0 24px 80px rgba(0,0,0,0.55)',
        padding: '18px 18px 16px'
      }}
    >
      <button
        aria-label="Close welcome message"
        type="button"
        onClick={onClose}
        style={{
          position: 'absolute',
          right: 14,
          top: 12,
          width: 34,
          height: 34,
          borderRadius: 999,
          border: '1px solid rgba(255,255,255,0.18)',
          background: 'rgba(255,255,255,0.08)',
          color: 'white',
          fontSize: 18,
          fontWeight: 900,
          cursor: 'pointer'
        }}
      >
        ×
      </button>
      <p className="eyebrow blue" style={{ marginBottom: 8 }}>Welcome to InQsi</p>
      <h3 style={{ margin: '0 42px 8px 0', fontSize: 'clamp(1.35rem, 4vw, 2rem)' }}>Your account is active.</h3>
      <p className="movement" style={{ marginBottom: 14 }}>
        {email ? `You are signed in as ${email}. ` : ''}Start by scanning a slip, building a slip, or opening the sports market board. One membership includes every supported sport.
      </p>
      <div className="hero-actions" style={{ gap: 10 }}>
        <Link className="primary-button large" href="/parlay-scanner" style={{ textDecoration: 'none' }}>Scan My Slip</Link>
        <Link className="ghost-button large" href="/parlays" style={{ textDecoration: 'none' }}>Build My Slip</Link>
        <Link className="ghost-button large" href="/sports/mlb" style={{ textDecoration: 'none' }}>Open Market Board</Link>
      </div>
    </div>
  );
}

export function AccountWorkspace() {
  const [session, setSession] = useState<MemberSession | null>(null);
  const [showWelcome, setShowWelcome] = useState(false);

  useEffect(() => {
    const activeSession = getMemberSession();
    setSession(activeSession);
    if (activeSession) {
      const dismissed = window.localStorage.getItem('inqsi_welcome_float_dismissed');
      setShowWelcome(dismissed !== 'true');
    }
  }, []);

  function closeWelcome() {
    window.localStorage.setItem('inqsi_welcome_float_dismissed', 'true');
    setShowWelcome(false);
  }

  if (!session) {
    return (
      <>
        <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
          <p className="eyebrow blue">Member workspace</p>
          <TeamJerseyBadge abbr="IQ" tone="blue" number="00" />
          <h2>You are not signed in yet.</h2>
          <p className="hero-copy">Log in to open your scanner, builder, saved slips, watchlist, alerts, review history, and billing portal.</p>
          <div className="hero-actions">
            <Link className="primary-button large" href="/login" style={{ textDecoration: 'none' }}>Login</Link>
            <Link className="ghost-button large" href="/register" style={{ textDecoration: 'none' }}>Create account</Link>
          </div>
        </section>
      </>
    );
  }

  const isFullAccess = session.plan === 'Full Access' || session.plan === 'Master';

  return (
    <>
      {showWelcome && <WelcomeFloat email={session.email} onClose={closeWelcome} />}

      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Member workspace</p>
        <div className="team-badge-row" style={{ marginTop: 10 }}>
          <TeamJerseyBadge abbr="INQ" tone="gold" number="38" />
          <TeamJerseyBadge abbr="SCAN" tone="blue" number="1" />
          <TeamJerseyBadge abbr="BLD" tone="teal" number="2" />
        </div>
        <h2>Welcome back.</h2>
        <p className="hero-copy">You are signed in as {session.email}. Scan a slip, let InQsi build a slip, publish or hide saved slips, review accuracy, or manage billing from one account workspace.</p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/parlays" style={{ textDecoration: 'none' }}>Build My Slip</Link>
          <Link className="ghost-button large" href="/parlay-scanner" style={{ textDecoration: 'none' }}>Scan My Slip</Link>
          <Link className="ghost-button large" href="/sports/mlb" style={{ textDecoration: 'none' }}>Market Board</Link>
          <Link className="ghost-button large" href="/account/slips" style={{ textDecoration: 'none' }}>My Slips & Scores</Link>
          <Link className="ghost-button large" href="/account/billing" style={{ textDecoration: 'none' }}>Billing Portal</Link>
          <button
            className="ghost-button large"
            type="button"
            onClick={() => {
              clearMemberSession();
              window.localStorage.removeItem('inqsi_welcome_float_dismissed');
              window.location.href = '/';
            }}
          >
            Log out
          </button>
        </div>
      </section>

      <section className="status-row">
        <article className="status-card"><TeamJerseyBadge abbr="IN" tone="green" number="1" /><span>Status</span><strong>Signed in</strong><p>Account workspace is active.</p></article>
        <article className="status-card"><TeamJerseyBadge abbr="IQ" tone={isFullAccess ? 'gold' : 'blue'} number={isFullAccess ? '38' : '5'} /><span>Access</span><strong>{session.plan}</strong><p>{isFullAccess ? 'All supported sports, scanner, builder, slip scoring, and market review access.' : 'Member workspace access.'}</p></article>
        <article className="status-card"><TeamJerseyBadge abbr="SLIP" tone="teal" number="%" /><span>Slip Scores</span><strong>Private by default</strong><p>Customers choose whether saved slips are public or private.</p></article>
        <article className="status-card"><TeamJerseyBadge abbr="PAY" tone="mint" number="0" /><span>Card Data</span><strong>Not stored</strong><p>Payment methods are managed through the provider portal.</p></article>
      </section>

      <section className="content-grid" style={{ marginTop: 20 }}>
        <article className="panel">
          <div className="panel-header compact">
            <div>
              <p className="eyebrow blue">Account menu</p>
              <h3>Your InQsi workspace</h3>
            </div>
          </div>
          <div className="game-list">
            {workspaceLinks.map((item) => <WorkspaceLinkCard key={item.href} {...item} />)}
          </div>
        </article>

        <aside className="panel">
          <p className="eyebrow blue">Saved slips</p>
          <h3>Public or private. No comments yet.</h3>
          <p className="movement">Customers should be able to keep slips private or display selected slips publicly. For now, other customers cannot comment on slips.</p>
          <p className="movement">After games finish, InQsi should score the slip and show accuracy by individual parlay, 1 day, 1 week, 1 month, 3 months, and 1 year.</p>
          <Link className="primary-button" href="/account/slips" style={{ display: 'inline-block', marginTop: 14, textDecoration: 'none' }}>Open My Slips & Scores</Link>
        </aside>
      </section>
    </>
  );
}
