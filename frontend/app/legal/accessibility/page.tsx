import type { Metadata } from 'next';
import { LegalDoc } from '@/components/LegalDoc';

export const metadata: Metadata = {
  title: 'Accessibility Statement',
  description: 'Accessibility statement for InQsi.',
  alternates: { canonical: '/legal/accessibility' }
};

export default function AccessibilityPage() {
  return (
    <LegalDoc
      title="Accessibility Statement"
      updated="June 2026"
      intro="InQsi aims to provide a usable experience for visitors and members across modern devices, browsers, and assistive technologies."
      sections={[
        {
          title: 'Our commitment',
          body: [
            'We are working to make public pages, member pages, forms, buttons, charts, navigation, and legal content usable by people with a wide range of abilities and assistive technologies.',
            'We aim to improve keyboard navigation, focus states, contrast, labels, heading structure, form messages, responsive layout, and screen-reader clarity as the product matures.'
          ]
        },
        {
          title: 'Feedback',
          body: [
            'If you have trouble using any part of the site, please contact us with the page URL, browser or device, assistive technology used if applicable, and a short description of the issue.',
            'Accessibility requests can be sent through the Contact page or to support@inqsi.app when that mailbox is active.'
          ]
        }
      ]}
    />
  );
}
