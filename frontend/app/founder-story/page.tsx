import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Founder Story | InQsi',
  description: 'The founder story behind InQsi and the idea of slowing down before lock-in.',
  alternates: { canonical: '/founder-story' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/founder-story"
      eyebrow="Founder story"
      title="Why InQsi exists"
      intro="InQsi was built around a simple idea: the market is constantly sending signals, but the most important ones are often easy to overlook. The product helps customers slow down, review the slip, and learn from the result."
      sections={[
        { title: 'Slow down', copy: 'The goal is to review before acting.' },
        { title: 'Find the warning', copy: 'The market can leave clues that deserve attention.' },
        { title: 'Track the result', copy: 'Post-game review helps the customer learn.' },
        { title: 'Stay disciplined', copy: 'InQsi keeps build structure simple and visible.' }
      ]}
    />
  );
}
