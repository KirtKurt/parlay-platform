import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Line Movement Review',
  description: 'Review how market lines moved after your InQsi risk check and what the movement may reveal.',
  alternates: { canonical: '/clv' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/clv"
      eyebrow="Line movement review"
      title="See whether the market moved for you or against you."
      intro="Line Movement Review keeps this advanced idea simple. After you scan a slip or save a game, InQsi helps you look back at how the market moved and whether the original warning signs mattered."
      sections={[
        { title: 'Starting number', copy: 'Save the market position when you review a pick.' },
        { title: 'Later movement', copy: 'Compare that starting point with where the market moves later.' },
        { title: 'Signal quality', copy: 'Review whether steam, resistance, or instability gave you useful clues.' },
        { title: 'Better discipline', copy: 'Use the history to learn when the market was helping you and when it was warning you off.' }
      ]}
    />
  );
}
