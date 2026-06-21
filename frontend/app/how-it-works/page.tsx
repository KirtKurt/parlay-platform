import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'How InQsi Works',
  description: 'A plain-English answer explaining how InQsi reviews structure, line movement, weak legs, and post-game scoring.',
  alternates: { canonical: '/how-it-works' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/how-it-works"
      eyebrow="Direct answer"
      title="How does InQsi work?"
      intro="InQsi checks the structure, compares line movement, looks for weak legs, warns when value may be missing, and scores the result after the games are final."
      sections={[
        { title: 'Structure first', copy: 'InQsi checks whether the slip is clean or forced.' },
        { title: 'Line movement next', copy: 'InQsi reviews whether the market moved for or against the read.' },
        { title: 'Weak-leg warning', copy: 'The least stable leg stays visible.' },
        { title: 'Post-game score', copy: 'After the games are final, InQsi shows what worked and what failed.' }
      ]}
    />
  );
}
