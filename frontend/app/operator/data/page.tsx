import type { Metadata } from 'next';
import { OperatorNav } from '@/components/OperatorNav';

export const metadata: Metadata = { title: 'Data Operations', description: 'Internal data feed and market record dashboard.', robots: { index: false, follow: false } };

const items = [
  ['Odds feed', 'Waiting on Monday API upgrade and key update.'],
  ['Stored games', 'Counted from games table through operator summary.'],
  ['Stored snapshots', 'Counted from snapshots table through operator summary.'],
  ['Status rows', 'Counted from status table through operator summary.'],
  ['Failed pulls', 'Should be added to ingestion run reporting before launch.']
];

export default function OperatorDataPage() {
  return (
    <main className="inqsi-shell">
      <header className="inqsi-topbar"><a className="inqsi-brand" href="/"><span className="inqsi-logo-mark">Q</span><span><b>InQsi</b><small>Data Ops</small></span></a><OperatorNav /></header>
      <section className="inqsi-hero"><div className="inqsi-hero-card"><p className="inqsi-promo">Data health</p><h1>Market data operations.</h1><p>Monitor feed readiness, snapshot storage, market status rows, and ingestion health.</p></div><aside className="inqsi-signup-card"><h2>Monday dependency</h2><p>Odds API upgrade and key update are still required before live market records can be verified.</p></aside></section>
      <section className="inqsi-feature-grid">{items.map(([title, copy]) => <article key={title}><b>{title}</b><span>{copy}</span></article>)}</section>
    </main>
  );
}
