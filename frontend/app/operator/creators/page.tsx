import type { Metadata } from 'next';
import { CreatorManager } from '@/components/CreatorManager';
import { OperatorNav } from '@/components/OperatorNav';

export const metadata: Metadata = { title: 'Creator Operations', description: 'Internal creator tracking and reporting.', robots: { index: false, follow: false } };

const items = [
  ['Creator code setup', 'Create one unique code per creator and campaign.'],
  ['Share link', 'Use /c/code for a clean creator link.'],
  ['Visit capture', 'Global capture records ref, creator, promo, and code parameters.'],
  ['Member linking', 'Signup should lock one member to one creator.'],
  ['Metrics', 'Backend reports live members, canceled members, past-due members, and MRR cents by creator.']
];

export default function OperatorCreatorsPage() {
  return (
    <main className="inqsi-shell">
      <header className="inqsi-topbar"><a className="inqsi-brand" href="/"><span className="inqsi-logo-mark">Q</span><span><b>InQsi</b><small>Creator Ops</small></span></a><OperatorNav /></header>
      <CreatorManager />
      <section className="inqsi-hero"><div className="inqsi-hero-card"><p className="inqsi-promo">Creators</p><h1>Creator attribution control.</h1><p>Create codes, track visits, lock member attribution, and report creator performance from backend metrics.</p></div><aside className="inqsi-signup-card"><h2>Rule</h2><p>Promo code wins. Otherwise first valid creator link gets credit. One member locks to one creator.</p></aside></section>
      <section className="inqsi-feature-grid">{items.map(([title, copy]) => <article key={title}><b>{title}</b><span>{copy}</span></article>)}</section>
    </main>
  );
}
