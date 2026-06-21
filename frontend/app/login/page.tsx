import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Login',
  description: 'Log in to InQsi when authentication providers are connected.',
  alternates: { canonical: '/login' }
};

export default function LoginPage() {
  return (
    <InqsiSeoPage
      path="/login"
      eyebrow="Member access"
      title="Return to your InQsi dashboard."
      intro="Login is prepared for Google, Apple, and email providers. Until provider keys are connected, the interface clearly shows Working on it."
      sections={[
        { title: 'Saved dashboard', copy: 'Return to saved items and account views once auth is connected.' },
        { title: 'Provider readiness', copy: 'Google, Apple, and email access wait on configured keys.' },
        { title: 'Secure account path', copy: 'Private account areas stay separate from public pages.' },
        { title: 'Clear status', copy: 'Unavailable auth services remain clearly labeled.' }
      ]}
    />
  );
}
