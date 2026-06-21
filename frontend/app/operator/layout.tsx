import Link from 'next/link';
import { headers } from 'next/headers';

function canOpenDashboard() {
  const expected = process.env.INQSI_DASHBOARD_KEY;
  const supplied = headers().get('x-inqsi-dashboard-key');
  return Boolean(expected && supplied && supplied === expected);
}

function LockedDashboard() {
  return (
    <main className="inqsi-shell">
      <header className="inqsi-topbar">
        <Link className="inqsi-brand" href="/"><span className="inqsi-logo-mark">Q</span><span><b>InQsi</b><small>Restricted Operator</small></span></Link>
        <nav className="inqsi-nav-actions"><Link href="/">Home</Link><Link href="/sports">Sports</Link><Link href="/pricing">Pricing</Link></nav>
      </header>
      <section className="inqsi-hero">
        <div className="inqsi-hero-card">
          <p className="inqsi-promo">Restricted</p>
          <h1>Dashboard locked.</h1>
          <p>This internal dashboard is not available to customers.</p>
        </div>
      </section>
    </main>
  );
}

export default function OperatorLayout({ children }: { children: React.ReactNode }) {
  if (!canOpenDashboard()) return <LockedDashboard />;
  return <>{children}</>;
}
