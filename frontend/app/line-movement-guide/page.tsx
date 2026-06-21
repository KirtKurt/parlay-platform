import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Line Movement Guide | InQsi',
  description: 'Learn what line movement means and how InQsi helps customers understand market changes before lock-in.',
  alternates: { canonical: '/line-movement-guide' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/line-movement-guide"
      eyebrow="Line movement guide"
      title="What is line movement?"
      intro="Line movement is the change in a number or price before a game starts. InQsi helps customers see whether that move supports their read or warns them to slow down."
      sections={[
        { title: 'The number changes', copy: 'Prices, spreads, and totals can move before lock-in.' },
        { title: 'Movement can help or hurt', copy: 'A move may support a pick, weaken it, or create uncertainty.' },
        { title: 'Timing matters', copy: 'A late move can change how clean the slip looks.' },
        { title: 'InQsi keeps it visible', copy: 'Line Movement Review gives the customer a cleaner way to see the change.' }
      ]}
      faqs={[
        { question: 'What does line movement mean?', answer: 'Line movement means the price, spread, or total changed before the game starts.' },
        { question: 'What does it mean when odds move against you?', answer: 'It may mean the market is no longer supporting the same read, so the pick deserves another review.' }
      ]}
    />
  );
}
