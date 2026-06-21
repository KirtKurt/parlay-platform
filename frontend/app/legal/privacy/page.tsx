import type { Metadata } from 'next';
import { LegalDoc } from '@/components/LegalDoc';

export const metadata: Metadata = {
  title: 'Privacy Policy',
  description: 'Privacy Policy for InQsi sports market intelligence, analytics, account services, and consent controls.',
  alternates: { canonical: '/legal/privacy' }
};

export default function PrivacyPolicyPage() {
  return (
    <LegalDoc
      title="Privacy Policy"
      updated="June 2026"
      intro="This Privacy Policy explains what information InQsi may collect, how it is used, how it is protected, and the choices members and visitors have."
      sections={[
        {
          title: 'Information we collect',
          body: [
            'We may collect account information such as name, email address, login activity, selected plan, support messages, privacy requests, and product preferences when those services are connected.',
            'We may collect product usage data such as pages visited, sports viewed, saved items, notices, device and browser information, approximate location derived from IP address, timestamps, referral source, and interaction events used to improve the service.',
            'Payment information should be processed by a secure payment provider. InQsi should not store full card numbers, bank credentials, CVV codes, or sensitive payment authentication data in the frontend application.'
          ]
        },
        {
          title: 'Analytics, pixels, and replay',
          body: [
            'With user consent where required, InQsi may use analytics tools, advertising pixels, and masked session replay to understand traffic, improve product design, measure campaigns, diagnose errors, and support product decisions.',
            'Session replay should mask email, password, payment, and user-entered fields. The product is configured to mark form fields as private and to avoid recording sensitive input values.'
          ]
        },
        {
          title: 'How we use information',
          body: [
            'We use information to create and manage accounts, provide member access, personalize the product, maintain security, send service notices, support promotional and subscription workflows, and improve performance.',
            'We may use aggregated or de-identified analytics to understand feature usage, troubleshoot errors, measure marketing performance, and improve content and product flows.'
          ]
        },
        {
          title: 'Sharing and service providers',
          body: [
            'We may share limited information with vendors that help operate the service, including hosting, analytics, authentication, customer support, email, payment, fraud prevention, advertising measurement, and data infrastructure providers.',
            'We do not knowingly sell personal information for cash. If advertising pixels or cross-context advertising tools are used, users should be offered a Do Not Sell or Share choice as required by applicable law.'
          ]
        },
        {
          title: 'Cookies and local storage',
          body: [
            'We may use cookies, local storage, pixels, and similar technologies to keep users signed in, remember preferences, store consent choices, measure traffic, detect abuse, and understand how visitors move through the site.',
            'Users can change privacy choices from the Privacy choices control or browser settings. Some account features may not work correctly if necessary storage is blocked.'
          ]
        },
        {
          title: 'User rights and requests',
          body: [
            'Depending on location, users may have rights to request access, correction, deletion, portability, restriction, or opt-out of certain sharing. InQsi provides data deletion and data export request pages.',
            'We may need to verify identity before fulfilling privacy requests. Certain records may be retained when required for security, legal, tax, billing, dispute, or operational reasons.'
          ]
        },
        {
          title: 'Children and age restrictions',
          body: [
            'InQsi is not intended for children. Users must meet the minimum age required by the site rules and their jurisdiction to use sports market intelligence connected to wagering-related decision making.',
            'If we learn that a minor provided personal information in violation of our rules, we may suspend or delete the account and related data as appropriate.'
          ]
        },
        {
          title: 'Contact',
          body: [
            'Privacy questions can be sent through the Contact page or to support@inqsi.app when that mailbox is active.'
          ]
        }
      ]}
    />
  );
}
