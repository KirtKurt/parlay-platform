import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';

export const metadata = {
  title: 'My Slips | InQsi',
  description: 'View, track, save, and manage InQsi slips.',
  alternates: { canonical: '/account/slips' }
};

const slips = [
  { id: 'INQ-98231', status: 'Active', odds: '+342', stake: '$100.00', toWin: '$342.00' },
  { id: 'INQ-98102', status: 'Won', odds: '+248', stake: '$75.00', toWin: '$261.00' },
  { id: 'INQ-97931', status: 'Saved', odds: '+289', stake: '$50.00', toWin: '$144.50' }
];

export default function MySlipsPage() {
  return (
    <main className="shell">
      <AppHeader title="My Slips" />
      <nav className="inqsi-tabs" aria-label="Slip filters"><span className="active">All</span><span>Active</span><span>Settled</span><span>Saved</span></nav>
      <section className="panel" style={{ marginBottom: 18 }}>
        <div className="panel-header compact"><div><p className="eyebrow blue">My Slips</p><h2 style={{ margin: 0 }}>3 Slips</h2><p className="movement" style={{ marginBottom: 0 }}>View details, track results, save drafts, and manage public/private visibility.</p></div><Link className="inqsi-primary" href="/parlays" style={{ textDecoration: 'none' }}>Build Slip</Link></div>
      </section>
      <section className="game-list">
        {slips.map((slip) => (
          <article className="rank-card top-zone" key={slip.id}>
            <div className="rank-head"><span>3-leg slip</span><b>{slip.status}</b></div>
            <div className="market-row" style={{ marginTop: 12 }}>
              <div><span>Slip ID</span><strong>{slip.id}</strong><b>{slip.status}</b></div>
              <div><span>Odds</span><strong>Parlay</strong><b>{slip.odds}</b></div>
              <div><span>Stake</span><strong>{slip.stake}</strong><b>{slip.toWin}</b></div>
              <div><span>Visibility</span><strong>Private</strong><b>Owner</b></div>
            </div>
            <div className="game-topline" style={{ marginTop: 14 }}><span>Saved slip</span><Link href="/account/slips">View Details</Link></div>
          </article>
        ))}
      </section>
    </main>
  );
}
