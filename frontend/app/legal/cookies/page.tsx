import type { Metadata } from 'next';
import { LegalDoc } from '@/components/LegalDoc';

export const metadata: Metadata = {
  title: 'Cookie Policy',
  description: 'Cookie Policy for InQsi analytics, consent choices, advertising pixels, and local storage.',
  alternates: { canonical: '/legal/cookies' }
};

export default function CookiePolicyPage() {
  return (
    <LegalDoc
      title="Cookie Policy"
      updated="June 2026"
      intro="This Cookie Policy explains how InQsi uses cookies, pixels, local storage, and similar technologies."
      sections={[
        {
          title: 'Necessary storage',
          body: [
            'Necessary storage helps the site operate, remember privacy choices, maintain security, and support account sessions when authentication is connected.',
            'Necessary storage cannot be fully disabled from the InQsi banner because the site may need it to function.'
          ]
        },
        {
          title: 'Analytics storage',
          body: [
            'Analytics tools help InQsi understand page views, traffic sources, feature usage, funnel drop-off, device information, and product performance.',
            'Analytics tools should only load after consent where consent is required or when configured as permitted by applicable law.'
          ]
        },
        {
          title: 'Marketing pixels',
          body: [
            'Marketing pixels may help measure campaigns, build audiences, and understand advertising performance when configured.',
            'Users can decline marketing pixels from the cookie banner or privacy choices page.'
          ]
        },
        {
          title: 'Masked session replay',
          body: [
            'Session replay may help InQsi understand interface problems, broken flows, rage clicks, and mobile layout issues.',
            'Replay should mask email, password, payment, and user-entered fields. InQsi marks forms as private and configures replay masking where supported.'
          ]
        },
        {
          title: 'Changing choices',
          body: [
            'Users can reopen the Privacy choices control at any time to accept or reject non-essential tracking categories.',
            'Users may also use browser settings or device settings to block or delete cookies and similar technologies.'
          ]
        }
      ]}
    />
  );
}
