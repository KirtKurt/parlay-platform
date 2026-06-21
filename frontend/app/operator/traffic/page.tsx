import type { Metadata } from 'next';
import { OperatorNav } from '@/components/OperatorNav';
import { TRAFFIC_ATTRIBUTES } from '@/lib/traffic-dashboard';

export const metadata: Metadata = { title: 'Traffic Operations', description: 'Traffic and campaign attribution dashboard.', robots: { index: false, follow: false } };

export default function OperatorTrafficPage() {
  return (
    <main className="inqsi-shell">
      <header className="inqsi-topbar"><a className="inqsi-brand" href="/"><span className="inqsi-logo-mark">Q</span><span><b>InQsi</b><small>Traffic Ops</small></span></a><OperatorNav /></header>
      <section className="inqsi-hero"><div className="inqsi-hero-card"><p className="inqsi-promo">Traffic</p><h1>Traffic and attribution view.</h1><p>Track visitors, pages, campaigns, creator codes, signup movement, promo starts, and live member conversion.</p></div><aside className="inqsi-signup-card"><h2>Source of truth</h2><p>Traffic analytics should feed the dashboard, while creator payout attribution remains tied to member records.</p></aside></section>
      <section className="inqsi-feature-grid">{TRAFFIC_ATTRIBUTES.map((item) => <article key={item.key}><b>{item.label}</b><span>{item.description}</span></article>)}</section>
    </main>
  );
}
