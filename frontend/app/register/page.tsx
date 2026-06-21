import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Create Account',
  description: 'Create an InQsi account and start the 5-day free promo.',
  alternates: { canonical: '/register' }
};

export default function RegisterPage() {
  return (
    <InqsiSeoPage
      path="/register"
      eyebrow="Create account"
      title="Start your InQsi workspace."
      intro="Start with 5 days free. Scan a slip, review the sports board, and see whether InQsi helps you catch risk before you lock anything in."
      sections={[
        { title: 'Quick access', copy: 'Create your workspace and get to the scanner without a complicated setup.' },
        { title: 'AI Slip Scanner', copy: 'Bring the picks you already like and check for weak legs, resistance, and warning signs.' },
        { title: 'Sports board', copy: 'Review market pressure, support, and unusual movement across supported sports.' },
        { title: 'Full Access', copy: 'One membership opens the full InQsi workspace after the 5-day promo.' }
      ]}
      faqs={[
        { question: 'What should I do first?', answer: 'Start with the AI Slip Scanner. Bring a pick you already like and see where the market may be warning you.' },
        { question: 'Is the 5-day promo included?', answer: 'Yes. InQsi is built around a 5-day free promo before monthly membership.' }
      ]}
    />
  );
}
