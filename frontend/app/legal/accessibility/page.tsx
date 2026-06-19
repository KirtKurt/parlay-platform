import { AppHeader } from '@/components/AppHeader';

export default function AccessibilityPage() {
  return (
    <main className="shell legal-page">
      <AppHeader eyebrow="Silvers Syndicate" title="Accessibility" />
      <section className="panel legal-panel">
        <p className="eyebrow blue">Accessibility</p>
        <h2>Accessibility Statement</h2>
        <p>Silvers Syndicate aims to provide a usable experience across modern devices and assistive technologies.</p>
        <p>We will continue improving contrast, keyboard navigation, labels, and mobile layout as the product evolves.</p>
      </section>
    </main>
  );
}
