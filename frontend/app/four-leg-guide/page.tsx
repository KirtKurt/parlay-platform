import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Why 4-Leg Parlays Are Harder | InQsi',
  description: 'A plain-English explanation of why InQsi caps builder output at three legs.',
  alternates: { canonical: '/four-leg-guide' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/four-leg-guide"
      eyebrow="Plain-English guide"
      title="Why are 4-leg parlays harder?"
      intro="Every extra leg adds another thing that has to go right. InQsi keeps builder output capped at three legs so the customer can review the structure more clearly."
      sections={[
        { title: 'More legs means more outcomes', copy: 'A fourth leg adds another outcome that has to land.' },
        { title: 'Weak legs can hide', copy: 'The more legs added, the easier it is to overlook the weak one.' },
        { title: 'Review gets harder', copy: 'A smaller slip makes market warnings easier to see.' },
        { title: 'InQsi keeps the cap', copy: 'InQsi builder output stays at three legs maximum.' }
      ]}
    />
  );
}
