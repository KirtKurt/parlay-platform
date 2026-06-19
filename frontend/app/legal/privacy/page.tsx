import type { Metadata } from 'next';
import { LegalDoc } from '@/components/LegalDoc';

export const metadata: Metadata = {
  title: 'Privacy Policy',
  description: 'Privacy Policy for Silvers Syndicate sports market intelligence and member services.',
  alternates: { canonical: '/legal/privacy' }
};

export default function PrivacyPolicyPage() {
  return (
    <LegalDoc
      title="Privacy Policy"
      updated="June 2026"
      intro="This Privacy Policy explains what information Silvers Syndicate may collect, how it is used, how it is protected, and the choices members have."
      sections={[
        {
          title: 'Information we collect',
          body: [
            'We may collect account information such as name, email address, phone number, date of birth, state of residence, login activity, selected plan, support messages, and product preferences.',
            'We may collect product usage data such as pages visited, sports viewed, watchlist activity, device and browser information, approximate location derived from IP address, timestamps, referral source, and interaction events used to improve the service.',
            'Payment information should be processed by a secure payment provider. Silvers Syndicate should not store complete card numbers, bank credentials, CVV codes, or sensitive payment authentication data in the frontend application.'
          ]
        },
        {
          title: 'How we use information',
          body: [
            'We use information to create and manage accounts, provide member access, personalize the sports board, maintain security, send service notices, support free-week and subscription workflows, and improve product performance.',
            'We may use aggregated or de-identified analytics to understand feature usage, improve market-board design, troubleshoot errors, and measure the effectiveness of educational content and marketing pages.'
          ]
        },
        {
          title: 'Sharing and service providers',
          body: [
            'We may share limited information with vendors that help operate the service, including hosting, analytics, authentication, customer support, email, payment, fraud prevention, and data infrastructure providers.',
            'We do not sell personal information to sportsbooks, teams, leagues, or advertisers. We do not represent that any league, team, sportsbook, or data provider sponsors, endorses, or approves Silvers Syndicate.'
          ]
        },
        {
          title: 'Cookies and analytics',
          body: [
            'We may use cookies, local storage, pixels, and similar technologies to keep users signed in, remember preferences, measure traffic, detect abuse, and understand how visitors move through the site.',
            'Browser settings may allow users to block or delete cookies, but some parts of the member workspace may not function correctly without them.'
          ]
        },
        {
          title: 'Data security',
          body: [
            'We use reasonable administrative, technical, and organizational safeguards designed to protect member information. No internet service can guarantee complete security.',
            'Production authentication should use server-side identity controls, secure password handling, email verification, role-based access, logging, and account recovery controls before accepting real paid subscribers.'
          ]
        },
        {
          title: 'User rights and requests',
          body: [
            'Depending on location, users may have rights to request access, correction, deletion, portability, or restriction of personal information. Requests can be sent through the Contact page.',
            'We may need to verify identity before fulfilling privacy requests. Certain records may be retained when required for security, legal, tax, billing, dispute, or operational reasons.'
          ]
        },
        {
          title: 'Children and age restrictions',
          body: [
            'Silvers Syndicate is not intended for children. Users must meet the minimum age required by the site rules and their jurisdiction to use sports market intelligence connected to wagering-related decision making.',
            'If we learn that a minor provided personal information in violation of our rules, we may suspend or delete the account and related data as appropriate.'
          ]
        },
        {
          title: 'Contact',
          body: [
            'Privacy questions can be sent through the Contact page or to support@silverssyndicate.app when that mailbox is active.'
          ]
        }
      ]}
    />
  );
}
