import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'What Is InQsi?',
  description: 'A plain-English answer explaining what InQsi is and how it helps customers review slips and market movement.',
  alternates: { canonical: '/what-is-inqsi' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/what-is-inqsi"
      eyebrow="Direct answer"
      title="What is InQsi?"
      intro="InQsi is a sports market review platform. It helps customers scan slips, build disciplined 3-leg slips, review line movement, check best-line warnings, save slips, and track post-game score accuracy."
      sections={[
        { title: 'Review before lock-in', copy: 'InQsi helps customers slow down and review the market before making a decision.' },
        { title: '3-leg discipline', copy: 'InQsi builder output is capped at three legs.' },
        { title: 'Score history', copy: 'Customers can track post-game score history over time.' },
        { title: 'Public card optional', copy: 'Customers can choose whether to show public score cards.' }
      ]}
      faqs={[
        { question: 'Does InQsi place bets?', answer: 'No. InQsi is a review platform and does not place bets for customers.' },
        { question: 'Does InQsi connect to sportsbook accounts?', answer: 'No. InQsi does not require direct sportsbook account connection.' }
      ]}
    />
  );
}
