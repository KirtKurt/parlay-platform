import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';
import { ACCESS_READINESS } from '@/lib/inqsi-access-control';

export const metadata: Metadata = {
  title: 'Account Readiness',
  description: 'Review InQsi account, trial, and subscription readiness.',
  alternates: { canonical: '/account-readiness' }
};

export default function AccountReadinessPage() {
  return (
    <InqsiSeoPage
      path="/account-readiness"
      eyebrow="Account readiness"
      title="Account and member access gates."
      intro={`Current status: ${ACCESS_READINESS.status}. InQsi is prepared for account access, a 5-day trial window, and full-access member gates.`}
      sections={[
        { title: 'Authentication', copy: ACCESS_READINESS.authReady ? 'Auth provider configured.' : 'Working on it until auth keys are connected.' },
        { title: 'Member processor', copy: ACCESS_READINESS.processorReady ? 'Member processor configured.' : 'Working on it until member processor keys are connected.' },
        { title: 'Trial window', copy: `${ACCESS_READINESS.trialDays} days prepared in the access model.` },
        { title: 'Full-access gates', copy: 'Member-only pages can check access level before showing the paid workspace.' }
      ]}
    />
  );
}
