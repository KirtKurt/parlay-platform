import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Market Data',
  description: 'Review available market data and data status in InQsi.',
  alternates: { canonical: '/best-lines' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/best-lines"
      eyebrow="Market data"
      title="Market data review."
      intro="InQsi organizes available market data in a clean interface. If verified data is not available, the page says Working on it."
      sections={[
        { title: 'Data status', copy: 'See what is available and what is still being connected.' },
        { title: 'Provider readiness', copy: 'Review whether supported sources are returning usable information.' },
        { title: 'Movement context', copy: 'Understand how current information compares with earlier snapshots.' },
        { title: 'No filler values', copy: 'Unavailable fields are not filled with artificial data.' }
      ]}
    />
  );
}
