import { AppHeader } from '@/components/AppHeader';

export default function DisclaimerPage() {
  return (
    <main className="shell legal-page">
      <AppHeader eyebrow="Silvers Syndicate" title="Disclaimer" />
      <section className="panel legal-panel">
        <p className="eyebrow blue">Important notice</p>
        <h2>Informational use only</h2>
        <p>Silvers Syndicate provides sports market intelligence, line movement summaries, and risk classification tools for informational and entertainment purposes only.</p>
        <p>Nothing on this site is legal, financial, gambling, betting, investment, or professional advice. We do not guarantee outcomes, winnings, profits, or prediction accuracy.</p>
        <p>Users are responsible for complying with all laws and rules that apply in their location.</p>
      </section>
    </main>
  );
}
