import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Alerts',
  description: 'Review InQsi alerts for market movement, risk changes, and warning signs on the games you follow.',
  alternates: { canonical: '/alerts' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/alerts"
      eyebrow="Alerts"
      title="Know when the market starts warning you."
      intro="InQsi alerts are built to surface meaningful changes, not noise. Save the games and picks you care about, then review movement, resistance, and risk changes in one place."
      sections={[
        { title: 'Market movement', copy: 'See when a game starts moving in a way that could change your read.' },
        { title: 'Risk changes', copy: 'Catch resistance, volatility, or weak-leg pressure before you lock in.' },
        { title: 'Saved picks', copy: 'Keep alerts focused on the games and slips you actually care about.' },
        { title: 'No noise', copy: 'InQsi should slow you down only when the signal is worth reviewing.' }
      ]}
    />
  );
}
