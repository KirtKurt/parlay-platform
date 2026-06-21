import type { Metadata } from 'next';
import { OperatorNav } from '@/components/OperatorNav';

export const metadata: Metadata = { title: 'Support Operations', description: 'Internal support operations dashboard.', robots: { index: false, follow: false } };

const items = [
  ['Support inbox', 'Connect support mailbox or ticketing system before launch.'],
  ['Account help', 'Route login, membership, cancellation, and access issues.'],
  ['Data issues', 'Route bad feed, missing game, and market-data reports.'],
  ['Creator issues', 'Route referral code and payout questions.']
];

export default function OperatorSupportPage() {
  return (
    <main className="inqsi-shell">
      <header className="inqsi-topbar"><a className="inqsi-brand" href="/"><span className="inqsi-logo-mark">Q</span><span><b>InQsi</b><small>Support Ops</small></span></a><OperatorNav /></header>
      <section className="inqsi-hero"><div className="inqsi-hero-card"><p className="inqsi-promo">Support</p><h1>Support routing center.</h1><p>Prepare account, data, creator, and privacy support routing before traffic starts.</p></div><aside className="inqsi-signup-card"><h2>Status</h2><p>Working on it until mailbox or ticketing system is connected.</p></aside></section>
      <section className="inqsi-feature-grid">{items.map(([title, copy]) => <article key={title}><b>{title}</b><span>{copy}</span></article>)}</section>
    </main>
  );
}
