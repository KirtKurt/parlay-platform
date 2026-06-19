import Link from 'next/link';

export function PromoBanner() {
  return (
    <div className="promo-banner" role="note" aria-label="Launch promotion">
      <div>
        <strong>Launch promo: first week free.</strong>
        <span> Preview the terminal, build your first watchlist, and see how the market board works before monthly billing begins.</span>
      </div>
      <Link href="/register?promo=free-week" style={{ textDecoration: 'none' }}>Start free week</Link>
    </div>
  );
}
