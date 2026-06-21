import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Create Account',
  description: 'Create an InQsi account and start the 5-day free promo when authentication and billing providers are connected.',
  alternates: { canonical: '/register' }
};

export default function RegisterPage() {
  return (
    <InqsiSeoPage
      path="/register"
      eyebrow="Create account"
      title="Start your InQsi workspace."
      intro="The account flow is designed for Google, Apple, and email access with a 5-day free promo. Until provider keys are connected, the page stays in Working on it mode."
      sections={[
        { title: 'Google access', copy: 'Visual flow is ready. OAuth keys still need to be connected.' },
        { title: 'Apple access', copy: 'Visual flow is ready. Apple client settings still need to be connected.' },
        { title: 'Email access', copy: 'Email auth remains disabled until a provider is selected.' },
        { title: 'Subscription gate', copy: 'Plan logic is prepared and waits on billing keys.' }
      ]}
      faqs={[
        { question: 'Can users create accounts now?', answer: 'The interface is ready, but provider keys must be connected before live sign-up.' },
        { question: 'Is the 5-day promo included?', answer: 'Yes. The product flow is prepared around a 5-day free promo.' }
      ]}
    />
  );
}
