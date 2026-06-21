import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Live Market',
  description: 'Review current market movement, signal changes, and risk pressure in InQsi.',
  alternates: { canonical: '/live-market' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/live-market"
      eyebrow="Live market"
      title="See how the market is moving right now."
      intro="The live market page is built to help you spot pressure, resistance, and signal changes as the board develops. Use it to slow down before you trust a pick that may be moving against you."
      sections={[
        { title: 'Movement pressure', copy: 'See where a game is starting to shift and whether the move supports or weakens your read.' },
        { title: 'Signal changes', copy: 'Watch for changes in stability, resistance, steam, and coin-flip pressure.' },
        { title: 'Watchlist friendly', copy: 'Use your saved games to focus only on the markets you care about.' },
        { title: 'Clear wait states', copy: 'If verified market data is not available yet, InQsi tells you clearly instead of filling gaps.' }
      ]}
    />
  );
}
