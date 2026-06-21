import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Data Export Request',
  description: 'Request a copy of eligible InQsi account data and personal information.',
  alternates: { canonical: '/data-export' }
};

export default function DataExportPage() {
  return (
    <InqsiSeoPage
      path="/data-export"
      eyebrow="Privacy request"
      title="Request a copy of your InQsi data."
      intro="Use this process to request a copy of eligible account data and personal information. InQsi may verify identity before releasing account information."
      sections={[
        { title: 'Account data', copy: 'Export may include account profile, plan status, and support request records when available.' },
        { title: 'Product data', copy: 'Export may include saved items, preferences, and product activity records when available.' },
        { title: 'Verification', copy: 'Identity verification may be required before any export is released.' },
        { title: 'Delivery', copy: 'Eligible data is provided in a reasonable portable format when available.' }
      ]}
      faqs={[
        { question: 'How do I submit an export request?', answer: 'Use the Contact page or support@inqsi.app when the mailbox is active.' },
        { question: 'Will sensitive security records be included?', answer: 'Certain security, fraud, internal, or legally restricted records may not be included.' }
      ]}
    />
  );
}
