import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'InQsi Dashboard',
  description: 'Review InQsi dashboard history and data status.',
  alternates: { canonical: '/performance' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/performance"
      eyebrow="Dashboard"
      title="Dashboard history."
      intro="InQsi will show verified history and data status here. Until records are available, the page shows Working on it."
      sections={[
        { title: 'History', copy: 'Display verified records when available.' },
        { title: 'Separate views', copy: 'Keep each sport organized independently.' },
        { title: 'Review', copy: 'Show what changed over time.' },
        { title: 'Data status', copy: 'Make unavailable information clear.' }
      ]}
    />
  );
}
