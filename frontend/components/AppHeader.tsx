import Link from 'next/link';

export type AppHeaderProps = {
  eyebrow?: string;
  title?: string;
  active?: 'home' | 'arb' | 'scanner' | 'sports' | 'calculator' | 'pricing';
  apiStatus?: 'CONNECTED' | 'WAITING' | 'FAILED' | 'MOCK';
  apiDetail?: string;
};

const links = [
  { href: '/arbitrage-v2', label: 'ARB', key: 'arb' },
  { href: '/parlay-scanner', label: 'Slip Scanner', key: 'scanner' },
  { href: '/sports', label: 'Sports', key: 'sports' },
  { href: '/arbitrage-v2/calculator', label: 'Calculator', key: 'calculator' },
  { href: '/pricing', label: 'Pricing', key: 'pricing' },
] as const;

const bottom = [
  { href: '/', label: 'Home', key: 'home', icon: '⌂' },
  { href: '/arbitrage-v2', label: 'Arb', key: 'arb', icon: 'ϟ' },
  { href: '/parlay-scanner', label: 'Slip', key: 'scanner', icon: '▤' },
  { href: '/sports', label: 'Sports', key: 'sports', icon: '◫' },
  { href: '/account', label: 'More', key: 'more', icon: '•••' },
] as const;

export function AppHeader({ active, apiStatus, apiDetail }: AppHeaderProps) {
  const down = apiStatus === 'FAILED' || apiStatus === 'WAITING';
  return (
    <>
      <header className="mockup-header">
        <Link className="mockup-logo" href="/" aria-label="InQsi home">
          <span>In</span><span className="q">Q</span><span>si</span>
        </Link>
        <nav className="mockup-nav" aria-label="Primary navigation">
          {links.map((link) => (
            <Link className={active === link.key ? 'active' : ''} href={link.href} key={link.key}>
              {link.label}
            </Link>
          ))}
        </nav>
        <div className="mockup-header-actions">
          {apiStatus && (
            <span className={'mockup-status ' + (down ? 'down' : '')} title={apiDetail}>
              <i />{down ? 'Data syncing' : 'Live'}
            </span>
          )}
          <Link className="mockup-account" href="/account" aria-label="Account">KS</Link>
        </div>
      </header>
      <nav className="mockup-bottom-nav" aria-label="Mobile navigation">
        {bottom.map((link) => (
          <Link className={active === link.key ? 'active' : ''} href={link.href} key={link.key}>
            <b>{link.icon}</b><span>{link.label}</span>
          </Link>
        ))}
      </nav>
    </>
  );
}
