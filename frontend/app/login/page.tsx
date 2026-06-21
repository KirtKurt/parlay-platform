import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Login',
  description: 'Log in to InQsi to return to your scanner, watchlist, alerts, and market review workspace.',
  alternates: { canonical: '/login' }
};

export default function LoginPage() {
  return (
    <InqsiSeoPage
      path="/login"
      eyebrow="Member access"
      title="Return to your InQsi workspace."
      intro="Log in to return to your saved picks, watchlist, alerts, and market review tools. InQsi keeps your risk checks in one place so you can pick back up where you left off."
      sections={[
        { title: 'Saved picks', copy: 'Return to slips and market checks you want to review again.' },
        { title: 'Watchlist', copy: 'Keep the games and markets you care about close.' },
        { title: 'Alerts', copy: 'Review warning signs and market changes without digging through every board.' },
        { title: 'Full workspace', copy: 'Use one account to move between the scanner, sports boards, and review history.' }
      ]}
    />
  );
}
