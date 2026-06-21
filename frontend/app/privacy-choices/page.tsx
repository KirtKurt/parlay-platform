import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Privacy Choices',
  description: 'Manage InQsi privacy choices, consent preferences, and Do Not Sell or Share requests.',
  alternates: { canonical: '/privacy-choices' }
};

export default function PrivacyChoicesPage() {
  return (
    <InqsiSeoPage
      path="/privacy-choices"
      eyebrow="Privacy controls"
      title="Do Not Sell or Share My Personal Information."
      intro="Use the Privacy choices control to reject analytics, marketing pixels, and masked replay. This page also explains how to make opt-out, deletion, and export requests."
      sections={[
        { title: 'Reject marketing pixels', copy: 'Use the Privacy choices button to turn off marketing pixels and similar sharing tools.' },
        { title: 'Reject analytics', copy: 'Turn off non-essential analytics if you do not want product behavior events collected.' },
        { title: 'Reject replay', copy: 'Turn off masked session replay if you do not want product replay diagnostics.' },
        { title: 'Submit requests', copy: 'Use the data deletion and data export pages for account-level privacy requests.' }
      ]}
      faqs={[
        { question: 'Where is the consent control?', answer: 'A Privacy choices button appears on the site after the banner is closed.' },
        { question: 'Does this page sell data?', answer: 'No. It provides opt-out controls and explains choices for advertising and sharing tools.' }
      ]}
    />
  );
}
