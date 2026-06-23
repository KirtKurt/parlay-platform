import Link from 'next/link';

export type AppHeaderProps = {
  eyebrow?: string;
  title?: string;
  apiStatus?: 'CONNECTED' | 'WAITING' | 'FAILED' | 'MOCK';
  apiDetail?: string;
};

const menuLinks = [
  { href: '/', label: 'Home' },
  { href: '/sports/mlb', label: 'Markets' },
  { href: '/parlays', label: 'Parlays' },
  { href: '/parlay-scanner', label: 'Scan' },
  { href: '/account', label: 'Account' },
  { href: '/login', label: 'Login' },
  { href: '/register', label: 'Start Membership' }
];

const bottomLinks = [
  { href: '/', label: 'Home', icon: 'H' },
  { href: '/sports/mlb', label: 'Markets', icon: 'M' },
  { href: '/parlays', label: 'Parlays', icon: 'P' },
  { href: '/parlay-scanner', label: 'Scan', icon: 'S' },
  { href: '/account', label: 'Account', icon: 'A' }
];

function readableStatus(status?: string) {
  if (!status || status === 'FAILED') return null;
  if (status === 'CONNECTED') return 'Live';
  if (status === 'WAITING') return 'Syncing';
  if (status === 'MOCK') return 'Preview';
  return status;
}

export function AppHeader({ eyebrow = 'InQsi', title = 'Sports market intelligence', apiStatus, apiDetail }: AppHeaderProps) {
  const status = readableStatus(apiStatus);
  return (
    <>
      <header className="inqsi-topbar inqsi-mobile-header">
        <Link className="inqsi-menu-button" href="/account" aria-label="Open menu">Menu</Link>
        <Link className="inqsi-wordmark" href="/" aria-label="InQsi home"><span>IN</span><b>Q</b><span>IS</span></Link>
        <Link className="inqsi-bell" href="/alerts" aria-label="Alerts">Alerts</Link>
      </header>
      <nav className="inqsi-desktop-links" aria-label="Site navigation">
        {status && <span className={`api-badge api-${apiStatus?.toLowerCase()}`} title={apiDetail}>{status}</span>}
        {menuLinks.map((link) => <Link href={link.href} key={link.href}>{link.label}</Link>)}
      </nav>
      <nav className="inqsi-bottom-nav" aria-label="Primary app navigation">
        {bottomLinks.map((link) => <Link href={link.href} key={link.href}><span>{link.icon}</span><small>{link.label}</small></Link>)}
      </nav>
    </>
  );
}
