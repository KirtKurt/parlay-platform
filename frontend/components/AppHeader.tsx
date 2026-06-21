import Link from 'next/link';

export type AppHeaderProps = {
  eyebrow?: string;
  title?: string;
  apiStatus?: 'CONNECTED' | 'WAITING' | 'FAILED' | 'MOCK';
  apiDetail?: string;
};

export function AppHeader({ eyebrow = 'InQsi', title = 'Sports market intelligence', apiStatus, apiDetail }: AppHeaderProps) {
  return (
    <nav className="topbar">
      <Link className="brand-block" href="/" style={{ color: 'inherit', textDecoration: 'none' }}>
        <div className="brand-mark">Q</div>
        <div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1></div>
      </Link>
      <div className="nav-actions">
        {apiStatus && <span className={`api-badge api-${apiStatus.toLowerCase()}`} title={apiDetail}>{apiStatus}</span>}
        <Link className="ghost-button" href="/start-here" style={{ textDecoration: 'none' }}>Start Here</Link>
        <Link className="ghost-button" href="/picks-audit" style={{ textDecoration: 'none' }}>Check Slip</Link>
        <Link className="ghost-button" href="/sports" style={{ textDecoration: 'none' }}>Sports</Link>
        <Link className="ghost-button" href="/pricing" style={{ textDecoration: 'none' }}>Pricing</Link>
        <Link className="primary-button" href="/register" style={{ textDecoration: 'none' }}>Start 5 Days Free</Link>
      </div>
    </nav>
  );
}
