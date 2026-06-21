import type { Metadata } from 'next';
import { OperatorNav } from '@/components/OperatorNav';

export const metadata: Metadata = { title: 'Privacy Operations', description: 'Internal privacy request dashboard.', robots: { index: false, follow: false } };

const items = [
  ['Deletion requests', 'Intake exists; final queue connection still needed.'],
  ['Export requests', 'Intake exists; final queue connection still needed.'],
  ['Consent choices', 'Consent banner and privacy choices page are built.'],
  ['Legal review', 'Final counsel review still required before paid traffic.']
];

export default function OperatorPrivacyPage() {
  return (
    <main className="inqsi-shell">
      <header className="inqsi-topbar"><a className="inqsi-brand" href="/"><span className="inqsi-logo-mark">Q</span><span><b>InQsi</b><small>Privacy Ops</small></span></a><OperatorNav /></header>
      <section className="inqsi-hero"><div className="inqsi-hero-card"><p className="inqsi-promo">Privacy</p><h1>Privacy and data request control.</h1><p>Track deletion/export readiness, consent controls, and legal review status.</p></div><aside className="inqsi-signup-card"><h2>Status</h2><p>Needs final support queue connection and legal review.</p></aside></section>
      <section className="inqsi-feature-grid">{items.map(([title, copy]) => <article key={title}><b>{title}</b><span>{copy}</span></article>)}</section>
    </main>
  );
}
