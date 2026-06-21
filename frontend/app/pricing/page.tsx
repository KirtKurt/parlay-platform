import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Pricing',
  description: 'Start InQsi with 5 days free and one $38 monthly full-access membership.',
  alternates: { canonical: '/pricing' }
};

export default function PricingPage() {
  return (
    <InqsiSeoPage
      path="/pricing"
      eyebrow="5 days free"
      title="One package. Full access. $38/month."
      intro="Start with 5 days free. After that, InQsi is one simple $38 monthly membership with access to the scanner, sports boards, alerts, watchlists, and market review tools."
      sections={[
        { title: '$38/month', copy: 'One full-access monthly membership. No confusing tiers.' },
        { title: 'AI Slip Scanner', copy: 'Bring the picks you already like and check where the slip may be weaker than it feels.' },
        { title: 'Market boards', copy: 'Review sports boards, game leans, market signals, best lines, and movement context.' },
        { title: '5-day promo', copy: 'Try the workspace first, then decide whether InQsi earns a place in your routine.' }
      ]}
      faqs={[
        { question: 'Are there multiple tiers?', answer: 'No. InQsi uses one full-access monthly package.' },
        { question: 'What is the monthly price?', answer: '$38 per month after the 5-day promo.' },
        { question: 'What should I try first?', answer: 'Start with the AI Slip Scanner, then review the sports board for the games you care about.' }
      ]}
    />
  );
}
