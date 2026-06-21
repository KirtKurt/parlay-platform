import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Best Lines',
  description: 'Compare available market prices and review line movement in InQsi before making a decision.',
  alternates: { canonical: '/best-lines' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/best-lines"
      eyebrow="Best lines"
      title="Compare the number before you trust the pick."
      intro="Small price differences can change how strong a slip really is. InQsi helps you review available lines, compare market movement, and spot where the number may be working for or against you."
      sections={[
        { title: 'Available prices', copy: 'Review the market numbers available for the games you care about.' },
        { title: 'Line movement', copy: 'See whether the market is moving toward or away from your side.' },
        { title: 'Risk context', copy: 'Use the number as part of the larger risk check, not as a blind signal.' },
        { title: 'No filler', copy: 'If a line is unavailable, InQsi should leave it clear instead of making one up.' }
      ]}
    />
  );
}
