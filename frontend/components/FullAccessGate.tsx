import { MemberStatus, gateForStatus } from '@/lib/inqsi-premium-gates';

export function FullAccessGate({ status = 'anonymous' as MemberStatus, children }: { status?: MemberStatus; children: React.ReactNode }) {
  const gate = gateForStatus(status);
  if (gate.allowed) return <>{children}</>;
  return (
    <section className="inqsi-hero">
      <div className="inqsi-hero-card">
        <p className="inqsi-promo">Full Access</p>
        <h2>{gate.plan.priceLabel}</h2>
        <p>{gate.message}</p>
        <a className="inqsi-primary" href="/pricing">View full-access package</a>
      </div>
      <aside className="inqsi-signup-card">
        <h2>Included</h2>
        {gate.plan.includes.map((item) => <p key={item}>{item}</p>)}
      </aside>
    </section>
  );
}
