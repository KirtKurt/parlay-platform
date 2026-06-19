import Link from 'next/link';

export function ComplianceFooter() {
  return (
    <footer className="site-footer">
      <div>
        <strong>Silvers Syndicate</strong>
        <p>Sports market intelligence for informational and entertainment use.</p>
      </div>
      <nav>
        <Link href="/legal/privacy">Privacy</Link>
        <Link href="/legal/site-terms">Site Rules</Link>
        <Link href="/legal/disclaimer">Disclaimer</Link>
        <Link href="/legal/safe-use">Safe Use</Link>
        <Link href="/legal/accessibility">Accessibility</Link>
        <Link href="/contact">Contact</Link>
      </nav>
    </footer>
  );
}
