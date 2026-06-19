import { AppHeader } from '@/components/AppHeader';

export default function SafeUsePage() {
  return (
    <main className="shell legal-page">
      <AppHeader eyebrow="Silvers Syndicate" title="Safe Use" />
      <section className="panel legal-panel">
        <p className="eyebrow blue">Safe use</p>
        <h2>Use the platform responsibly</h2>
        <p>Silvers Syndicate is a market-intelligence and education tool.</p>
        <p>Use only where the product is permitted.</p>
      </section>
    </main>
  );
}
