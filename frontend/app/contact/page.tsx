import { AppHeader } from '@/components/AppHeader';

export default function ContactPage() {
  return (
    <main className="shell legal-page">
      <AppHeader eyebrow="Silvers Syndicate" title="Contact" />
      <section className="panel legal-panel">
        <p className="eyebrow blue">Contact</p>
        <h2>Contact Silvers Syndicate</h2>
        <p>Use this page for account, access, product, and business inquiries.</p>
        <p>A live contact form and support routing will be connected when the backend account system is finalized.</p>
      </section>
    </main>
  );
}
