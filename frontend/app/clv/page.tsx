import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'CLV Tracking',
  description: 'Review CLV tracking readiness, snapshot timing, and data status in InQsi.',
  alternates: { canonical: '/clv' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/clv"
      eyebrow="CLV"
      title="Closing line value tracking."
      intro="InQsi will compare saved market snapshots with later market positions when verified data is available. Until that data exists, this page shows Working on it."
      sections={[
        { title: 'Snapshot tracking', copy: 'Store the market position at the time of review.' },
        { title: 'Later comparison', copy: 'Compare saved snapshots with later market positions when available.' },
        { title: 'Signal review', copy: 'Use movement quality to review whether a signal had useful timing.' },
        { title: 'Data status', copy: 'Show unavailable information clearly instead of filling gaps.' }
      ]}
    />
  );
}
