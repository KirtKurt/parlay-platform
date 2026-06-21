import type { Metadata } from 'next';
import { OperatorNav } from '@/components/OperatorNav';

export const metadata: Metadata = { title: 'Attribution Operations', description: 'Internal creator attribution dashboard.', robots: { index: false, follow: false } };

const items = [
  ['Captured visits', 'Store landing page, visitor ID, UTM fields, and creator code.'],
  ['Locked attribution', 'Once a member is linked, attribution does not move unless manually corrected.'],
  ['Campaign reporting', 'Measure creator traffic and conversion by code.'],
  ['Privacy', 'Default reports should show aggregate counts, not personal member data.']
];

export default function OperatorAttributionPage() {
  return (
    <main className="inqsi-shell">
      <header className="inqsi-topbar"><a className="inqsi-brand" href="/"><span className="inqsi-logo-mark">Q</span><span><b>InQsi</b><small>Attribution Ops</small></span></a><OperatorNav /></header>
      <section className="inqsi-hero"><div className="inqsi-hero-card"><p className="inqsi-promo">Attribution</p><h1>Track where members came from.</h1><p>Creator links, campaign codes, and member attribution are ready for operator reporting.</p></div><aside className="inqsi-signup-card"><h2>Clean credit</h2><p>Promo code wins. First valid creator click is backup attribution.</p></aside></section>
      <section className="inqsi-feature-grid">{items.map(([title, copy]) => <article key={title}><b>{title}</b><span>{copy}</span></article>)}</section>
    </main>
  );
}
