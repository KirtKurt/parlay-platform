import Link from 'next/link';
import { HeaderAuthControls } from '@/components/HeaderAuthControls';
import { PromoBanner } from '@/components/PromoBanner';

export type AppHeaderProps = {
  eyebrow?: string;
  title?: string;
  apiStatus?: 'CONNECTED' | 'MOCK' | 'FAILED';
  apiDetail?: string;
};

export function AppHeader({
  eyebrow = 'Silvers Syndicate',
  title = 'Sports market intelligence',
  apiStatus,
  apiDetail
}: AppHeaderProps) {
  return (
    <>
      <PromoBanner />
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
          <Link className="ghost-button" href="/start-here" style={{ textDecoration: 'none' }}>Start Here</Link>
          <Link className="ghost-button" href="/picks-audit" style={{ textDecoration: 'none' }}>Test Picks</Link>
          <Link className="ghost-button" href="/sports" style={{ textDecoration: 'none' }}>Sports</Link>
          <Link className="ghost-button" href="/pricing" style={{ textDecoration: 'none' }}>Pricing</Link>
          <HeaderAuthControls />
          <Link className="primary-button" href="/register?promo=free-week" style={{ textDecoration: 'none' }}>Start Free Week</Link>
        </div>
      </nav>
    </>
  );
}
