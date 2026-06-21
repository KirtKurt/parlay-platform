import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Does InQsi Place Bets?',
  description: 'A direct answer: InQsi reviews slips and market movement, but does not place bets for customers.',
  alternates: { canonical: '/does-inqsi-place-bets' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/does-inqsi-place-bets"
      eyebrow="Direct answer"
      title="Does InQsi place bets?"
      intro="No. InQsi does not place bets for customers. InQsi is built to review slips, line movement, best-line warnings, saved score history, and post-game results."
      sections={[
        { title: 'Review platform', copy: 'InQsi helps customers review before and after games.' },
        { title: 'Customer controlled', copy: 'The customer controls what they do outside InQsi.' },
        { title: 'No sportsbook login', copy: 'InQsi does not require customers to connect sportsbook accounts.' },
        { title: 'No wager placement', copy: 'InQsi does not transmit or place wagers.' }
      ]}
    />
  );
}
