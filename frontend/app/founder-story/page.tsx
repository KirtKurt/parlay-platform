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
      intro="InQsi was built around a simple idea: the market is constantly sending signals, but the most important ones are often easy to overlook. The product helps customers slow down, review the slip, understand what can go right, understand what can go wrong, and learn from the result."
      sections={[
        { title: 'Teach both sides', copy: 'InQsi helps customers understand the possible upside while also making the downside and uncertainty easier to see.' },
        { title: 'Show the warning', copy: 'The most useful review is often the one that shows where the market may be warning the customer to slow down.' },
        { title: 'Avoid noise', copy: 'InQsi is not built around fast side-market features. The product stays focused on structure, clarity, and review discipline.' },
        { title: 'Stay disciplined', copy: 'InQsi keeps build structure simple and visible so customers can learn from the result.' }
      ]}
    />
  );
}
