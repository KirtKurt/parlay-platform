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
          <TeamJerseyBadge abbr="IQ" tone="blue" number="00" />
          <h2>You are not signed in yet.</h2>
          <p className="hero-copy">Log in to open your scanner, builder, watchlist, alerts, review history, and billing portal.</p>
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
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Member workspace</p>
        <div className="team-badge-row" style={{ marginTop: 10 }}>
          <TeamJerseyBadge abbr="INQ" tone="gold" number="38" />
          <TeamJerseyBadge abbr="SCAN" tone="blue" number="1" />
          <TeamJerseyBadge abbr="BLD" tone="teal" number="2" />
        </div>
        <h2>Welcome back.</h2>
        <p className="hero-copy">You are signed in as {session.email}. Scan a slip, let InQsi build a slip, review market movement, or manage billing from one account workspace.</p>
        <div className="hero-actions">
          <Link className="primary-button large" href="/parlays" style={{ textDecoration: 'none' }}>Build My Slip</Link>
          <Link className="ghost-button large" href="/parlay-scanner" style={{ textDecoration: 'none' }}>Scan My Slip</Link>
          <Link className="ghost-button large" href="/account/billing" style={{ textDecoration: 'none' }}>Billing Portal</Link>
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
        <article className="status-card"><TeamJerseyBadge abbr="IN" tone="green" number="1" /><span>Status</span><strong>Signed in</strong><p>Account workspace is active.</p></article>
        <article className="status-card"><TeamJerseyBadge abbr="IQ" tone={isFullAccess ? 'gold' : 'blue'} number={isFullAccess ? '38' : '5'} /><span>Access</span><strong>{session.plan}</strong><p>{isFullAccess ? 'Full scanner, builder, and market review access.' : 'Member workspace access.'}</p></article>
        <article className="status-card"><TeamJerseyBadge abbr="DAY" tone="teal" number="5" /><span>Promo Ends</span><strong>{formatDate(session.promoEndsAt)}</strong><p>The payment provider will enforce the real renewal date.</p></article>
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
          <p className="eyebrow blue">Billing & payment</p>
          <h3>Use the secure provider portal.</h3>
          <p className="movement">Members should be able to update their card, view invoices, manage renewal, and change subscription settings through the payment provider portal.</p>
          <p className="movement">InQsi should store access status and provider customer IDs only. It should not store raw credit card numbers.</p>
          <Link className="primary-button" href="/account/billing" style={{ display: 'inline-block', marginTop: 14, textDecoration: 'none' }}>Open Billing & Payment Portal</Link>
        </aside>
      </section>
    </>
  );
}
