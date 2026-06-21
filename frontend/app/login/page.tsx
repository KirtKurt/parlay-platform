import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Login',
  description: 'Log in to InQsi to return to your saved slips, watchlists, alerts, and market review tools.',
  alternates: { canonical: '/login' }
};

export default function LoginPage() {
  return (
    <InqsiSeoPage
      path="/login"
      eyebrow="Member access"
      title="Return to your InQsi workspace."
      intro="Log in to get back to your saved slips, watchlists, alerts, and market review tools. Start with the picks you care about and keep your risk checks organized."
      sections={[
        { title: 'Saved slips', copy: 'Return to the picks and boards you want to review again.' },
        { title: 'Watchlist', copy: 'Keep the games and markets you care about in one place.' },
        { title: 'Alerts', copy: 'Review important signal and market movement changes.' },
        { title: 'Full workspace', copy: 'Move between the AI Slip Scanner, sports boards, game leans, and review history without losing your place.' }
      ]}
    />
  );
}
