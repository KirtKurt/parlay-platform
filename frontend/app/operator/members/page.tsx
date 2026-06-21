import type { Metadata } from 'next';
import { OperatorNav } from '@/components/OperatorNav';

export const metadata: Metadata = { title: 'Member Operations', description: 'Internal member operations dashboard.', robots: { index: false, follow: false } };

const items = [
  ['Live members', 'Count members with live_paid status.'],
  ['Trials', 'Review members inside promotional access.'],
  ['Past due', 'Show members who need account attention.'],
  ['Canceled', 'Track churn and win-back opportunities.'],
  ['Session health', 'Review active sessions and session expiry.']
];

export default function OperatorMembersPage() {
  return (
    <main className="inqsi-shell">
      <header className="inqsi-topbar"><a className="inqsi-brand" href="/"><span className="inqsi-logo-mark">Q</span><span><b>InQsi</b><small>Member Ops</small></span></a><OperatorNav /></header>
      <section className="inqsi-hero"><div className="inqsi-hero-card"><p className="inqsi-promo">Members</p><h1>Member status control.</h1><p>Monitor live members, trials, past-due states, cancellations, and account sessions.</p></div><aside className="inqsi-signup-card"><h2>Source of truth</h2><p>Member status comes from the membership webhook and member session tables.</p></aside></section>
      <section className="inqsi-feature-grid">{items.map(([title, copy]) => <article key={title}><b>{title}</b><span>{copy}</span></article>)}</section>
    </main>
  );
}
