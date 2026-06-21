import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'AI Slip Scanner | InQsi',
  description: 'Use InQsi to scan a slip, review risk, check line movement, and find where a pick may be wrong before lock-in.',
  alternates: { canonical: '/ai-slip-scanner' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/ai-slip-scanner"
      eyebrow="AI Slip Scanner"
      title="AI Slip Scanner for risk review before lock-in"
      intro="The InQsi AI Slip Scanner helps customers review a slip before lock-in. It checks structure, line movement, best-line warnings, and hidden risk so a customer can see where the slip may be weak."
      sections={[
        { title: 'Scan the slip', copy: 'Start with the selections the customer already wants to review.' },
        { title: 'Find weak spots', copy: 'InQsi looks for unstable legs, market warnings, and structure risk.' },
        { title: 'Check the line', copy: 'The scanner can show when a better number may be available elsewhere.' },
        { title: 'Save and score', copy: 'Saved slips can be reviewed after the games are final.' }
      ]}
      faqs={[
        { question: 'What is an AI Slip Scanner?', answer: 'An AI Slip Scanner reviews a customer-entered slip for structure, market movement, best-line warnings, and risk before lock-in.' },
        { question: 'Does InQsi place bets?', answer: 'No. InQsi reviews slips and market signals. The customer controls what they do elsewhere.' }
      ]}
    />
  );
}
