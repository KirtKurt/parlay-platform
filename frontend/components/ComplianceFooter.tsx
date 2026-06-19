import Link from 'next/link';

export function ComplianceFooter() {
  return (
    <footer className="site-footer">
      <div>
        <strong>Silvers Syndicate</strong>
        <p>Sports market intelligence for informational and entertainment use. Not financial, legal, gambling, or betting advice.</p>
      </div>
      <nav>
        <Link href="/legal/privacy">Privacy</Link>
        <Link href="/legal/terms">Terms</Link>
        <Link href="/legal/disclaimer">Disclaimer</Link>
        <Link href="/legal/responsible-use">Responsible Use</Link>
        <Link href="/legal/accessibility">Accessibility</Link>
        <Link href="/contact">Contact</Link>
      </nav>
    </footer>
  );
}
