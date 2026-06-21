import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Pricing',
  description: 'Start InQsi with a 5-day free promo and review the premium market intelligence subscription plan.',
  alternates: { canonical: '/pricing' }
};

export default function PricingPage() {
  return (
    <InqsiSeoPage
      path="/pricing"
      eyebrow="5 days free"
      title="Simple InQsi pricing."
      intro="Start with 5 days free. InQsi Premium is designed for market movement review, signal context, saved lists, notices, dashboard history, and a clean mobile-first workflow. Billing activates only after provider keys and subscription settings are connected."
      sections={[
        { title: '5-day promo', copy: 'New members begin with a 5-day promotional access window.' },
        { title: 'Premium workflow', copy: 'Designed for market board review, signal context, saved lists, and dashboard history.' },
        { title: 'Billing readiness', copy: 'Subscription checkout stays in Working on it mode until billing keys are connected.' },
        { title: 'No fake access state', copy: 'The app clearly labels unavailable account and payment services.' }
      ]}
      faqs={[
        { question: 'Is billing live now?', answer: 'Billing is marked Working on it until Stripe or another billing provider is connected.' },
        { question: 'How long is the promo?', answer: 'The current product direction is a 5-day free promo.' },
        { question: 'Can users manage accounts now?', answer: 'Account controls are designed in the interface, but provider keys still need to be connected.' }
      ]}
    />
  );
}
