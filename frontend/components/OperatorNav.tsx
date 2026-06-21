import { OPERATOR_NAV } from '@/lib/inqsi-operator-dashboard';

export function OperatorNav() {
  return (
    <nav className="inqsi-nav-actions" aria-label="Operator navigation">
      {OPERATOR_NAV.map((item) => <a key={item.href} href={item.href}>{item.label}</a>)}
    </nav>
  );
}
