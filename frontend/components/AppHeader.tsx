import Link from 'next/link';

export type AppHeaderProps = {
  eyebrow?: string;
  title?: string;
  apiStatus?: 'CONNECTED' | 'WAITING' | 'FAILED' | 'MOCK';
  apiDetail?: string;
};

const primaryLinks = [
  { href: '/sports', label: 'Sports' },
  { href: '/parlay-scanner', label: 'AI Slip Scanner' },
  { href: '/parlays', label: 'AI Slip Builder' },
  { href: '/pricing', label: 'Pricing' }
];

const menuLinks = [
  { href: '/', label: 'Home' },
  { href: '/parlay-scanner', label: 'AI Slip Scanner' },
  { href: '/parlays', label: 'AI Slip Builder' },
  { href: '/sports', label: 'Sports board' },
  { href: '/game-leans', label: 'Game Leans' },
  { href: '/best-lines', label: 'Best lines' },
  { href: '/live-market', label: 'Live market' },
  { href: '/line-movement-review', label: 'Line Movement Review' },
  { href: '/alerts', label: 'Alerts' },
  { href: '/watchlist', label: 'Watchlist' },
  { href: '/performance', label: 'Review History' },
  { href: '/methodology', label: 'Methodology' },
  { href: '/login', label: 'Login' },
  { href: '/register', label: 'Start 5 days free' }
];

export function AppHeader({ eyebrow = 'InQsi', title = 'Sports market intelligence', apiStatus, apiDetail }: AppHeaderProps) {
  return (
    <header className="inqsi-topbar inqsi-global-nav">
      <Link className="inqsi-brand" href="/" aria-label="InQsi home">
        <span className="inqsi-logo-mark" aria-hidden="true">Q</span>
        <span><b>{eyebrow}</b><small>{title}</small></span>
      </Link>

      <nav className="inqsi-nav-actions inqsi-desktop-nav" aria-label="Primary navigation">
        {apiStatus && <span className={`api-badge api-${apiStatus.toLowerCase()}`} title={apiDetail}>{apiStatus}</span>}
        {primaryLinks.map((link) => <Link href={link.href} key={link.href}>{link.label}</Link>)}
        <Link className="inqsi-primary" href="/register">Start 5 Days Free</Link>
      </nav>

      <details className="inqsi-menu">
        <summary aria-label="Open navigation menu">Menu</summary>
        <nav aria-label="Site navigation menu">
          {apiStatus && <span className={`api-badge api-${apiStatus.toLowerCase()}`} title={apiDetail}>{apiStatus}</span>}
          {menuLinks.map((link) => <Link href={link.href} key={link.href}>{link.label}</Link>)}
        </nav>
      </details>
    </header>
  );
}
