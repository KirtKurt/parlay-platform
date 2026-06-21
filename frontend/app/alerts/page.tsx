import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Notices',
  description: 'Review notices, data status, and update readiness in InQsi.',
  alternates: { canonical: '/alerts' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/alerts"
      eyebrow="Notices"
      title="Notices and update status."
      intro="InQsi will show important update notices when verified data is connected. If the feed is unavailable, the page shows Working on it."
      sections={[
        { title: 'Update notices', copy: 'Show meaningful changes for saved items.' },
        { title: 'Status changes', copy: 'Highlight when data status changes.' },
        { title: 'Timing', copy: 'Organize notices around recent updates.' },
        { title: 'Clarity', copy: 'Avoid filler notices when no verified change is available.' }
      ]}
    />
  );
}
