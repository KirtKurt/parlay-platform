import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Account Connection | InQsi',
  description: 'A direct answer explaining that InQsi does not require customers to connect external accounts.',
  alternates: { canonical: '/account-connection' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/account-connection"
      eyebrow="Direct answer"
      title="Does InQsi require account connection?"
      intro="No external account connection is required. InQsi is focused on market review, customer-entered slips, saved score history, and post-game review."
      sections={[
        { title: 'No outside login required', copy: 'Customers do not need to provide outside credentials.' },
        { title: 'Review focus', copy: 'InQsi reviews movement and slip structure.' },
        { title: 'Customer privacy', copy: 'Saved slips can stay private by default.' },
        { title: 'Future-ready', copy: 'Any future sync feature should be user-permissioned and compliant.' }
      ]}
    />
  );
}
