import { AppHeader } from '@/components/AppHeader';

export default function SiteTermsPage() {
  return (
    <main className="shell legal-page">
      <AppHeader eyebrow="Silvers Syndicate" title="Site Rules" />
      <section className="panel legal-panel">
        <p className="eyebrow blue">Rules</p>
        <h2>Site Rules</h2>
        <p>Silvers Syndicate is built for sports market research, education, and entertainment.</p>
        <p>The platform organizes market movement and risk labels. It does not promise results.</p>
        <p>Service features may change as the product evolves.</p>
      </section>
    </main>
  );
}
