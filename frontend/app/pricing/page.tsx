import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Pricing',
  description: 'Start InQsi with a 5-day free promo and review the premium market intelligence membership plan.',
  alternates: { canonical: '/pricing' }
};

export default function PricingPage() {
  return (
    <InqsiSeoPage
      path="/pricing"
      eyebrow="5 days free"
      title="Simple InQsi pricing."
      intro="Start with 5 days free. InQsi Premium is designed for market movement review, signal context, saved lists, notices, dashboard history, and a clean mobile-first workflow. Member activation waits until the selected account service is connected."
      sections={[
        { title: '5-day promo', copy: 'New members begin with a 5-day promotional access window.' },
        { title: 'Premium workflow', copy: 'Designed for market board review, signal context, saved lists, and dashboard history.' },
        { title: 'Member readiness', copy: 'Membership remains in Working on it mode until the account service is connected.' },
        { title: 'No fake access state', copy: 'The app clearly labels unavailable account services.' }
      ]}
      faqs={[
        { question: 'Is member activation live now?', answer: 'Member activation is marked Working on it until the selected account service is connected.' },
        { question: 'How long is the promo?', answer: 'The current product direction is a 5-day free promo.' },
        { question: 'Can users manage accounts now?', answer: 'Account controls are designed in the interface, but provider keys still need to be connected.' }
      ]}
    />
  );
}
