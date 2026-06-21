import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Live Status Mode',
  description: 'Review current status, signal changes, and verified data availability in InQsi.',
  alternates: { canonical: '/live-market' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/live-market"
      eyebrow="Live status"
      title="Live status mode."
      intro="InQsi is designed to show current status and signal changes when supported data is connected. Missing feeds show Working on it."
      sections={[
        { title: 'Current status', copy: 'Show active status when supported by connected providers.' },
        { title: 'Signal changes', copy: 'Highlight when stability or movement changes.' },
        { title: 'Watchlist friendly', copy: 'Give users a focused place to review what they are following.' },
        { title: 'Clear unavailable state', copy: 'If live data is not connected, InQsi does not invent it.' }
      ]}
    />
  );
}
