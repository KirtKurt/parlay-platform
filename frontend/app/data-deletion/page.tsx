import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Data Deletion Request',
  description: 'Request deletion of InQsi account data and personal information where available by law.',
  alternates: { canonical: '/data-deletion' }
};

export default function DataDeletionPage() {
  return (
    <InqsiSeoPage
      path="/data-deletion"
      eyebrow="Privacy request"
      title="Request deletion of your InQsi data."
      intro="Use this process to request deletion of account data and personal information. Some records may be retained when required for security, legal, billing, tax, dispute, or operational reasons."
      sections={[
        { title: 'Step 1', copy: 'Send the request from the email address tied to your InQsi account.' },
        { title: 'Step 2', copy: 'InQsi may verify identity before processing the request.' },
        { title: 'Step 3', copy: 'Eligible account and product data is deleted or de-identified.' },
        { title: 'Step 4', copy: 'A completion notice is sent when the request is processed.' }
      ]}
      faqs={[
        { question: 'Can all records be deleted?', answer: 'Some records may need to be retained for security, billing, tax, dispute, or legal reasons.' },
        { question: 'How do I submit the request?', answer: 'Use the Contact page or support@inqsi.app when the mailbox is active.' }
      ]}
    />
  );
}
