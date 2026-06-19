import Link from 'next/link';

export type AppHeaderProps = {
  eyebrow?: string;
  title?: string;
  apiStatus?: 'CONNECTED' | 'MOCK' | 'FAILED';
  apiDetail?: string;
};

export function AppHeader({
  eyebrow = 'Silvers Syndicate',
  title = 'Sportsbook-style parlay intelligence',
  apiStatus,
  apiDetail
}: AppHeaderProps) {
  return (
    <nav className="topbar">
      <Link className="brand-block" href="/" style={{ color: 'inherit', textDecoration: 'none' }}>
        <div className="brand-mark">SS</div>
        <div>
          <p className="eyebrow">{eyebrow}</p>
          <h1>{title}</h1>
        </div>
      </Link>
      <div className="nav-actions">
        {apiStatus && <span className={`api-badge api-${apiStatus.toLowerCase()}`} title={apiDetail}>{apiStatus}</span>}
        <Link className="ghost-button" href="/sports" style={{ textDecoration: 'none' }}>Sports</Link>
        <Link className="ghost-button" href="/methodology" style={{ textDecoration: 'none' }}>Methodology</Link>
        <Link className="ghost-button" href="/pricing" style={{ textDecoration: 'none' }}>Pricing</Link>
        <Link className="ghost-button" href="/login" style={{ textDecoration: 'none' }}>Login</Link>
        <Link className="primary-button" href="/register" style={{ textDecoration: 'none' }}>Join</Link>
      </div>
    </nav>
  );
}
