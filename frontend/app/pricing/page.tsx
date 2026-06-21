import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Pricing',
  description: 'Start InQsi with a 5-day free promo and one $38 monthly full-access membership.',
  alternates: { canonical: '/pricing' }
};

export default function PricingPage() {
  return (
    <InqsiSeoPage
      path="/pricing"
      eyebrow="5 days free"
      title="One package. Full access. $38/month."
      intro="InQsi has one simple membership: $38 per month for all available features. No tiers. No feature splitting. Start with 5 days free."
      sections={[
        { title: '$38/month', copy: 'One monthly full-access membership package.' },
        { title: 'All features included', copy: 'Market boards, signal context, alerts, watchlists, dashboard history, best-line display, and supported 3-leg ranking tools.' },
        { title: '5-day promo', copy: 'New members begin with a 5-day promotional access window.' },
        { title: 'Member readiness', copy: 'Membership remains in Working on it mode until the selected account service is connected.' }
      ]}
      faqs={[
        { question: 'Are there multiple tiers?', answer: 'No. InQsi uses one full-access monthly package.' },
        { question: 'What is the monthly price?', answer: '$38 per month.' },
        { question: 'How long is the promo?', answer: 'The current product direction is a 5-day free promo.' }
      ]}
    />
  );
}
