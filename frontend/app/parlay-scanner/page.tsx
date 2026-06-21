import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Structure Scanner',
  description: 'Review structure, signal quality, weak points, and data readiness in InQsi.',
  alternates: { canonical: '/parlay-scanner' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/parlay-scanner"
      eyebrow="Structure scanner"
      title="Structure scanner and signal review."
      intro="InQsi reviews structure and signal quality in a clean interface. If verified data is unavailable, the page says Working on it."
      sections={[
        { title: 'Structure review', copy: 'Review how items fit together based on available signals.' },
        { title: 'Strongest area', copy: 'Show which area has the clearest support when data is available.' },
        { title: 'Weakest area', copy: 'Highlight which area needs the most review.' },
        { title: 'Ranked view', copy: 'Display ranked possibilities when the required information is available.' }
      ]}
    />
  );
}
