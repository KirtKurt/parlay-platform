import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Three Leg Cap | InQsi',
  description: 'A plain-English answer explaining why InQsi keeps builder output capped at three legs.',
  alternates: { canonical: '/three-leg-cap' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/three-leg-cap"
      eyebrow="Direct answer"
      title="Why does InQsi cap builds at 3 legs?"
      intro="InQsi caps builder output at 3 legs because smaller slips are easier to review. The customer can see the strongest legs, the weak leg, and the market warning without hiding risk inside a larger build."
      sections={[
        { title: 'Simple structure', copy: 'Three legs are easier to review than larger slips.' },
        { title: 'Weak leg visible', copy: 'The weakest part stays easier to spot.' },
        { title: 'Eight outcome paths', copy: 'A 3-leg slip still has eight possible outcome paths.' },
        { title: 'No forced build', copy: 'InQsi should warn the customer when the structure is not clean.' }
      ]}
    />
  );
}
