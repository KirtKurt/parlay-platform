'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { clearMemberSession, getMemberSession, MemberSession } from '@/lib/memberSession';

const workspaceLinks = [
  { href: '/sports/mlb', chip: 'Markets', title: 'Sports Market Board', copy: 'Live ML, spread, total, book count, and market signals.' },
  { href: '/parlays', chip: 'Parlays', title: 'Official Hourly Parlays', copy: '3-leg structures built from pull history and market discipline.' },
  { href: '/parlay-scanner', chip: 'Scan', title: 'Scan My Slip', copy: 'Review your own picks for weak legs and market warning signs.' },
  { href: '/account/slips', chip: 'Slips', title: 'My Slips', copy: 'View, track, manage, and score saved slips.' },
  { href: '/performance', chip: 'Stats', title: 'Review History', copy: 'Track accuracy windows and scoring progress.' },
  { href: '/account/billing', chip: 'Billing', title: 'Billing Portal', copy: 'Manage subscription and payment settings.' }
];

function WorkspaceLinkCard({ href, chip, title, copy }: { href: string; chip: string; title: string; copy: string }) {
  return (
    <Link className="game-card" href={href} style={{ color: 'inherit', textDecoration: 'none' }}>
      <div className="game-topline"><span className="league-chip">{chip}</span><span>Open</span></div>
      <h4>{title}</h4>
      <p className="movement">{copy}</p>
    </Link>
  );
}

function WelcomeFloat({ email, onClose }: { email?: string; onClose: () => void }) {
  return (
    <div role="dialog" aria-label="Welcome to InQsi" className="panel" style={{ position: 'fixed', left: '50%', bottom: 'max(96px, env(safe-area-inset-bottom))', transform: 'translateX(-50%)', zIndex: 70, width: 'min(720px, calc(100vw - 28px))', padding: 18 }}>
      <button aria-label="Close welcome message" type="button" onClick={onClose} style={{ position: 'absolute', right: 14, top: 12, width: 34, height: 34, borderRadius: 999, border: '1px solid rgba(255,255,255,0.18)', background: 'rgba(255,255,255,0.08)', color: 'white', fontSize: 18, fontWeight: 900, cursor: 'pointer' }}>×</button>
      <p className="eyebrow blue">Welcome to InQsi</p>
      <h3 style={{ marginRight: 42 }}>Your account is active.</h3>
      <p className="movement">{email ? `You are signed in as ${email}. ` : ''}Start with the market board, official parlays, or scanner. One membership includes every supported sport.</p>
      <div className="hero-actions">
        <Link className="inqsi-primary" href="/sports/mlb">Open Market Board</Link>
        <Link className="ghost-button" href="/parlays">Official Parlays</Link>
        <Link className="ghost-button" href="/parlay-scanner">Scan My Slip</Link>
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
    if (activeSession) setShowWelcome(window.localStorage.getItem('inqsi_welcome_float_dismissed') !== 'true');
  }, []);

  function closeWelcome() {
    window.localStorage.setItem('inqsi_welcome_float_dismissed', 'true');
    setShowWelcome(false);
  }

  if (!session) {
    return (
      <section className="panel" style={{ marginBottom: 20 }}>
        <p className="eyebrow blue">Member workspace</p>
        <h2>You are not signed in yet.</h2>
        <p className="movement">Log in to open your scanner, market board, official parlays, saved slips, watchlist, alerts, review history, and billing portal.</p>
        <div className="hero-actions"><Link className="inqsi-primary" href="/login">Login</Link><Link className="ghost-button" href="/register">Create Account</Link></div>
      </section>
    );
  }

  const isFullAccess = session.plan === 'Full Access' || session.plan === 'Master';

  return (
    <>
      {showWelcome && <WelcomeFloat email={session.email} onClose={closeWelcome} />}

      <section className="panel" style={{ marginBottom: 18 }}>
        <div className="panel-header compact">
          <div>
            <p className="eyebrow blue">Member Profile</p>
            <h2 style={{ margin: 0 }}>{session.email?.split('@')[0] || 'InQsi Member'}</h2>
            <p className="movement" style={{ marginBottom: 0 }}>Numbers. Discipline. Edge. Turning market data into smarter decisions.</p>
          </div>
          <span className="data-status">Approved</span>
        </div>
      </section>

      <section className="status-row">
        <article className="status-card"><span>Status</span><strong>Signed in</strong><p>Account workspace is active.</p></article>
        <article className="status-card"><span>Access</span><strong>{session.plan}</strong><p>{isFullAccess ? 'All supported sports, scanner, builder, slip scoring, and market review.' : 'Member workspace access.'}</p></article>
        <article className="status-card"><span>Win Rate</span><strong>64.3%</strong><p>Profile mockup metric.</p></article>
        <article className="status-card"><span>Confidence</span><strong>82</strong><p>Very strong profile mockup score.</p></article>
      </section>

      <section className="content-grid" style={{ marginTop: 20 }}>
        <article className="panel">
          <div className="panel-header compact"><div><p className="eyebrow blue">Workspace</p><h3>Your InQsi tools</h3></div></div>
          <div className="game-list">{workspaceLinks.map((item) => <WorkspaceLinkCard key={item.href} {...item} />)}</div>
        </article>
        <aside className="panel">
          <p className="eyebrow blue">My Slips</p>
          <h3>Public or private.</h3>
          <p className="movement">Saved slips are private by default. You choose what appears on your public profile.</p>
          <Link className="inqsi-primary" href="/account/slips" style={{ textDecoration: 'none', width: '100%' }}>Open My Slips</Link>
          <button className="ghost-button" type="button" style={{ width: '100%', marginTop: 12 }} onClick={() => { clearMemberSession(); window.localStorage.removeItem('inqsi_welcome_float_dismissed'); window.location.href = '/'; }}>Log out</button>
        </aside>
      </section>
    </>
  );
}
