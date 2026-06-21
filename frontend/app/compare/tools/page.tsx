import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'InQsi Tool Comparison',
  description: 'Compare InQsi with other tracking and review tools.',
  alternates: { canonical: '/compare/tools' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/compare/tools"
      eyebrow="Comparison guide"
      title="How InQsi is different"
      intro="InQsi is built for review, market warnings, score history, and post-game learning. The product keeps the customer focused on structure and discipline."
      sections={[
        { title: 'Review first', copy: 'InQsi helps customers review before and after games.' },
        { title: 'Score history', copy: 'InQsi makes result history easier to understand.' },
        { title: 'Clean experience', copy: 'InQsi avoids noisy social features at launch.' },
        { title: 'Simple structure', copy: 'InQsi keeps builder output capped at 3 legs.' }
      ]}
    />
  );
}
