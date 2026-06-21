import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Line Movement Review',
  description: 'Review how market lines moved after your first InQsi read.',
  alternates: { canonical: '/line-movement-review' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/line-movement-review"
      eyebrow="Line movement review"
      title="Review how the number moved after your first read."
      intro="Line Movement Review keeps this advanced idea simple. After you scan a slip or save a game, InQsi helps you look back at how the market moved and whether the original warning signs mattered."
      sections={[
        { title: 'Starting number', copy: 'Save the market position when you review a pick.' },
        { title: 'Later movement', copy: 'Compare that starting point with where the market moves later.' },
        { title: 'Signal quality', copy: 'Review whether steam, resistance, or instability gave you useful clues.' },
        { title: 'Better discipline', copy: 'Use review history to learn when the market was helping and when it was warning.' }
      ]}
    />
  );
}
