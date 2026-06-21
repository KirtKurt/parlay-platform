import { headers } from 'next/headers';

function canOpenDashboard() {
  const expected = process.env.INQSI_DASHBOARD_KEY;
  const supplied = headers().get('x-inqsi-dashboard-key');
  return Boolean(expected && supplied && supplied === expected);
}

function LockedDashboard() {
  return (
    <main className="inqsi-shell">
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
