import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Watchlist',
  description: 'Save the games, slips, and market signals you want to review again in InQsi.',
  alternates: { canonical: '/watchlist' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/watchlist"
      eyebrow="Watchlist"
      title="Keep the games you care about close."
      intro="Save the games, slips, and market signals you want to review again. Your watchlist keeps the important spots in one place so you can come back before lock-in."
      sections={[
        { title: 'Saved games', copy: 'Keep the matchups you are watching in one clean view.' },
        { title: 'Saved slips', copy: 'Return to the picks you want InQsi to challenge again.' },
        { title: 'Signal follow-up', copy: 'Track pressure, resistance, and warning signs without digging through every board.' },
        { title: 'Faster review', copy: 'Use the watchlist to focus only on the markets that matter to you.' }
      ]}
    />
  );
}
