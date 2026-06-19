import { AppHeader } from '@/components/AppHeader';

export default function PrivacyPage() {
  return (
    <main className="shell legal-page">
      <AppHeader eyebrow="Silvers Syndicate" title="Privacy Policy" />
      <section className="panel legal-panel">
        <p className="eyebrow blue">Privacy</p>
        <h2>Privacy Policy</h2>
        <p>Silvers Syndicate may collect account information, contact details, subscription status, device information, usage events, and preferences needed to operate the service.</p>
        <p>We use collected information to provide access, improve the platform, protect accounts, support customers, and maintain compliance records.</p>
        <p>Users may request account support or data questions through the contact page.</p>
      </section>
    </main>
  );
}
