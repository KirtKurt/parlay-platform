import Link from 'next/link';

export type AppHeaderProps = {
  eyebrow?: string;
  title?: string;
  apiStatus?: 'CONNECTED' | 'WAITING' | 'FAILED' | 'MOCK';
  apiDetail?: string;
};

const menuLinks = [
  { href: '/arbitrage-v2', label: 'ARB' },
  { href: '/parlay-scanner', label: 'Slip Scanner' },
  { href: '/sports', label: 'Sports' },
  { href: '/arbitrage-v2/calculator', label: 'Calculators' },
  { href: '/pricing', label: 'Pricing' },
  { href: '/account', label: 'Account' }
];

const bottomLinks = [
  { href: '/', label: 'Home', icon: '⌂' },
  { href: '/arbitrage-v2', label: 'ARB', icon: 'ϟ' },
  { href: '/parlay-scanner', label: 'Scan', icon: '▤' },
  { href: '/sports', label: 'Sports', icon: '▥' },
  { href: '/account', label: 'More', icon: '•••' }
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
        <Link className="inqsi-wordmark" href="/" aria-label="InQsi home"><span>In</span><b>Q<i aria-hidden="true">➤</i></b><span>si</span></Link>
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
