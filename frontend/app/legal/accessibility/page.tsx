import type { Metadata } from 'next';
import { LegalDoc } from '@/components/LegalDoc';

export const metadata: Metadata = {
  title: 'Accessibility Statement',
  description: 'Accessibility statement for Silvers Syndicate.',
  alternates: { canonical: '/legal/accessibility' }
};

export default function AccessibilityPage() {
  return (
    <LegalDoc
      title="Accessibility Statement"
      updated="June 2026"
      intro="Silvers Syndicate aims to provide a usable experience for all visitors and members across modern devices, browsers, and assistive technologies."
      sections={[
        {
          title: 'Our commitment',
          body: [
            'We are working to make public pages, member pages, forms, buttons, charts, navigation, and legal content usable by people with a wide range of abilities and assistive technologies.',
            'We aim to improve keyboard navigation, focus states, contrast, labels, heading structure, form messages, responsive layout, and screen-reader clarity as the product matures.'
          ]
        },
        {
          title: 'Current known limitations',
          body: [
            'Some chart and interactive data experiences may require additional accessible descriptions, keyboard controls, or alternate table views as new features are added.',
            'Some preview or demo components may change quickly while the product is being developed. We will continue improving accessibility before paid production launch.'
          ]
        },
        {
          title: 'Feedback',
          body: [
            'If you have trouble using any part of the site, please contact us with the page URL, browser or device, assistive technology used if applicable, and a short description of the issue.',
            'Accessibility requests can be sent through the Contact page or to support@silverssyndicate.app when that mailbox is active.'
          ]
        }
      ]}
    />
  );
}
