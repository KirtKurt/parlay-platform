import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Data Readiness',
  description: 'Review InQsi market data readiness.',
  alternates: { canonical: '/data-readiness' }
};

const sections = [
  { title: 'Market source', copy: 'Working on it until the odds feed key is connected.' },
  { title: 'Schedule source', copy: 'Working on it until the schedule feed key is connected.' },
  { title: 'Live status source', copy: 'Working on it until live status coverage is connected.' },
  { title: 'Context source', copy: 'Working on it until context data is connected.' }
];

export default function DataReadinessPage() {
  return (
    <InqsiSeoPage
      path="/data-readiness"
      eyebrow="Data readiness"
      title="Verified market data only."
      intro="InQsi is prepared for real market, schedule, live status, and context feeds. Missing feeds stay clearly labeled as Working on it."
      sections={sections}
    />
  );
}
