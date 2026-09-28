import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';

export const metadata = {
  title: 'My Slips',
  description: 'View, track, save, and manage InQsi slips.',
  alternates: { canonical: '/account/slips' }
};

export default function MySlipsPage() {
  return (
    <main className="shell">
      <AppHeader title="My Slips" />
      <nav className="inqsi-tabs" aria-label="Slip filters"><span className="active">All</span><span>Active</span><span>Settled</span><span>Saved</span></nav>
      <section className="panel" style={{ marginBottom: 18 }}>
        <div className="panel-header compact"><div><p className="eyebrow blue">My Slips</p><h2 style={{ margin: 0 }}>Waiting</h2><p className="movement" style={{ marginBottom: 0 }}>Saved slips appear here after you scan or build one. No sample tickets are shown.</p></div><Link className="inqsi-primary" href="/parlays" style={{ textDecoration: 'none' }}>Build Slip</Link></div>
      </section>
      <section className="game-list">
        <article className="rank-card">
          <div className="rank-head"><span>SLIPS</span><b>Waiting</b></div>
          <h4>No saved slips yet</h4>
          <p>Scan a ticket or build a parlay to see real odds, stake, and result here.</p>
        </article>
      </section>
    </main>
  );
}
