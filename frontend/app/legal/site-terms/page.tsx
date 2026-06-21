import type { Metadata } from 'next';
import { LegalDoc } from '@/components/LegalDoc';

export const metadata: Metadata = {
  title: 'Site Terms',
  description: 'Terms of use and site rules for InQsi.',
  alternates: { canonical: '/legal/site-terms' }
};

export default function SiteTermsPage() {
  return (
    <LegalDoc
      title="Site Terms"
      updated="June 2026"
      intro="These Site Terms govern access to and use of InQsi, including public pages, free previews, account pages, member tools, market boards, and member features."
      sections={[
        {
          title: 'Acceptance of terms',
          body: [
            'By accessing or using InQsi, you agree to these Site Terms, the Privacy Policy, the Disclaimer, and the Safe Use rules. If you do not agree, do not use the service.',
            'We may update these terms as the product, laws, provider relationships, or member features change. Continued use after updates means you accept the updated terms.'
          ]
        },
        {
          title: 'Nature of the service',
          body: [
            'InQsi provides sports market intelligence, educational content, line movement displays, risk labels, and research tools for informational and entertainment purposes.',
            'InQsi is not a sportsbook, does not accept wagers, does not place bets for users, does not custody funds, and does not guarantee any outcome, win, profit, ranking, signal, model result, or pick.'
          ]
        },
        {
          title: 'Eligibility and responsible use',
          body: [
            'You must be old enough and legally permitted in your jurisdiction to use sports market intelligence connected to wagering-related decision making. We may restrict, suspend, or terminate access where required or appropriate.',
            'You are solely responsible for complying with laws, sportsbook rules, league rules, tax obligations, and any other rules that apply to your conduct and location.'
          ]
        },
        {
          title: 'Accounts and security',
          body: [
            'You are responsible for maintaining the confidentiality of your login credentials and for activity under your account. Notify us promptly if you suspect unauthorized access.',
            'We may require email verification, stronger authentication, account recovery checks, device monitoring, or other security controls before production launch and at any time after launch.'
          ]
        },
        {
          title: 'Promotions, access, cancellation, and refunds',
          body: [
            'Promotional 5-day access may be offered to new members and may be modified, limited, or ended at any time. Any offer terms shown in the product will control that specific offer.',
            'Membership, cancellation, renewal, refund, and access rules must be presented clearly before paid access opens. Unless a separate written policy says otherwise, access fees are for digital services and may not be refundable after an access period begins.',
            'A user must cancel through the stated cancellation method before the renewal date to avoid future charges once live member access is active.'
          ]
        },
        {
          title: 'Prohibited conduct',
          body: [
            'You may not copy, scrape, reverse engineer, resell, sublicense, exploit, overload, attack, or interfere with the service. You may not use the service to violate law, harass others, commit fraud, evade compliance controls, or misrepresent affiliation with InQsi.',
            'You may not use automated tools to harvest data, reconstruct proprietary rankings, bypass access gates, or create competing datasets or products without written permission.'
          ]
        },
        {
          title: 'Intellectual property and marks',
          body: [
            'InQsi owns or licenses the site design, text, software, graphics, custom icons, workflows, risk labels, presentation format, and proprietary methods used in the service.',
            'Team names, league names, and sportsbook names may be used only for identification, analysis, comparison, or navigation. InQsi is not affiliated with, endorsed by, sponsored by, or approved by any league, team, sportsbook, or data provider.'
          ]
        },
        {
          title: 'No warranties and limitation of liability',
          body: [
            'The service is provided on an as-is and as-available basis. We do not warrant uninterrupted access, error-free data, timely updates, accurate odds, profitable outcomes, or that every market movement will be captured.',
            'To the fullest extent permitted by law, InQsi will not be liable for losses, wagers, decisions, damages, lost profits, lost data, business interruption, or indirect, incidental, consequential, special, punitive, or exemplary damages arising from use of the service.'
          ]
        },
        {
          title: 'Termination',
          body: [
            'We may suspend, limit, or terminate access for violations of these terms, suspected abuse, non-payment, security concerns, legal risk, provider restrictions, or conduct that threatens the service or other users.'
          ]
        }
      ]}
    />
  );
}
