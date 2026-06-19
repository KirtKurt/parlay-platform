import type { Metadata } from 'next';
import { AppHeader } from '@/components/AppHeader';

export const metadata: Metadata = {
  title: 'Contact',
  description: 'Contact Silvers Syndicate for account, access, product, legal, privacy, and business questions.',
  alternates: { canonical: '/contact' }
};

export default function ContactPage() {
  return (
    <main className="shell legal-shell">
      <AppHeader eyebrow="Silvers Syndicate" title="Contact" />
      <section className="legal-hero panel">
        <p className="eyebrow blue">Support and inquiries</p>
        <h2>Contact Silvers Syndicate</h2>
        <p>Use this page for account access, subscription questions, legal notices, privacy requests, accessibility feedback, product issues, partnership questions, and business inquiries.</p>
      </section>

      <section className="legal-doc-grid">
        <article className="panel legal-doc">
          <section>
            <h3>Support</h3>
            <p>For account, login, plan, or product questions, contact support@silverssyndicate.app when that mailbox is active.</p>
            <p>Please include the email connected to your account, the page URL, the browser or device, and a short description of the issue.</p>
          </section>
          <section>
            <h3>Legal and privacy requests</h3>
            <p>For privacy, legal, accessibility, intellectual-property, or data requests, use the same support mailbox and clearly mark the subject line with the type of request.</p>
            <p>We may need to verify identity or ownership before acting on account, data, or legal requests.</p>
          </section>
          <section>
            <h3>Business inquiries</h3>
            <p>For partnerships, data-provider discussions, media, affiliate inquiries, or enterprise conversations, include your company name, role, and the reason for outreach.</p>
          </section>
        </article>
        <aside className="panel legal-note">
          <h3>Before launch</h3>
          <p>A live form, ticket routing, and verified support inbox should be connected before paid production subscribers are opened.</p>
          <p>For now, this page gives search engines and users a permanent destination for contact and compliance routing.</p>
        </aside>
      </section>
    </main>
  );
}
