import Link from 'next/link';

export type AppHeaderProps = {
  eyebrow?: string;
  title?: string;
  apiStatus?: 'CONNECTED' | 'WAITING' | 'FAILED' | 'MOCK';
  apiDetail?: string;
};

export function AppHeader({ eyebrow = 'InQsi', title = 'Sports market intelligence', apiStatus, apiDetail }: AppHeaderProps) {
  return (
    <header className="inqsi-topbar">
      <Link className="inqsi-brand" href="/" aria-label="InQsi home">
        <span className="inqsi-logo-mark" aria-hidden="true">Q</span>
        <span><b>{eyebrow}</b><small>{title}</small></span>
      </Link>
      <nav className="inqsi-nav-actions" aria-label="Primary navigation">
        {apiStatus && <span className={`api-badge api-${apiStatus.toLowerCase()}`} title={apiDetail}>{apiStatus}</span>}
        <Link href="/sports">Sports</Link>
        <Link href="/parlay-scanner">Scanner</Link>
        <Link href="/performance">Performance</Link>
        <Link href="/pricing">Pricing</Link>
        <Link className="inqsi-primary" href="/register">Start 5 Days Free</Link>
      </nav>
    </header>
  );
}
