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
      title="Account and subscription gates."
      intro={`Current status: ${ACCESS_READINESS.status}. InQsi is prepared for account access, a 5-day trial window, and premium access gates.`}
      sections={[
        { title: 'Authentication', copy: ACCESS_READINESS.authReady ? 'Auth provider configured.' : 'Working on it until auth keys are connected.' },
        { title: 'Billing', copy: ACCESS_READINESS.billingReady ? 'Billing provider configured.' : 'Working on it until billing keys are connected.' },
        { title: 'Trial window', copy: `${ACCESS_READINESS.trialDays} days prepared in the access model.` },
        { title: 'Premium gates', copy: 'Premium-only pages can check access level before showing member workspace.' }
      ]}
    />
  );
}
