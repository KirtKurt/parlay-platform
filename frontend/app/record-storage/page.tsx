import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';
import { RECORD_STORAGE_TARGET } from '@/lib/inqsi-record-store';

export const metadata: Metadata = {
  title: 'Record Storage',
  description: 'Review InQsi record storage readiness.',
  alternates: { canonical: '/record-storage' }
};

export default function RecordStoragePage() {
  return (
    <InqsiSeoPage
      path="/record-storage"
      eyebrow="Record storage"
      title="Verified records only."
      intro={`Storage status: ${RECORD_STORAGE_TARGET.status}. InQsi is prepared to store verified feed records with no invented values.`}
      sections={[
        { title: 'Table target', copy: `Environment key: ${RECORD_STORAGE_TARGET.tableEnvKey}` },
        { title: 'Cadence', copy: RECORD_STORAGE_TARGET.cadence },
        { title: 'No fallback policy', copy: RECORD_STORAGE_TARGET.noFallbackPolicy },
        { title: 'Lookup design', copy: 'Records are keyed by sport, date, item, feed, time, and category.' }
      ]}
    />
  );
}
