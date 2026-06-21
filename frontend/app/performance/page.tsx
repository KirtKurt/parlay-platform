import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Review History',
  description: 'Review your InQsi history, saved market reads, and past risk checks.',
  alternates: { canonical: '/performance' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/performance"
      eyebrow="Review history"
      title="Look back at what the market showed you."
      intro="Review History helps you revisit past slips, saved games, and market reads. Use it to see what signals were present, what changed later, and where InQsi helped you slow down before lock-in."
      sections={[
        { title: 'Past reviews', copy: 'Return to the slips, boards, and risk checks you saved.' },
        { title: 'Signal history', copy: 'See the market context that was visible when you reviewed a pick.' },
        { title: 'Sport-by-sport view', copy: 'Keep football, basketball, hockey, baseball, soccer, tennis, and other sports organized separately.' },
        { title: 'Cleaner decisions', copy: 'Use past reviews to understand which warnings mattered and which reads stayed clean.' }
      ]}
    />
  );
}
