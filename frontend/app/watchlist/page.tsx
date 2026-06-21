import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Saved List',
  description: 'Review saved items, data readiness, and status in InQsi.',
  alternates: { canonical: '/watchlist' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/watchlist"
      eyebrow="Saved list"
      title="Saved items and review status."
      intro="InQsi will keep followed items organized in one place. If connected data is unavailable, the page shows Working on it."
      sections={[
        { title: 'Saved items', copy: 'Keep important items organized in one dashboard.' },
        { title: 'Status view', copy: 'See whether each saved item has current verified data.' },
        { title: 'Quick return', copy: 'Return to important pages without searching.' },
        { title: 'Clear states', copy: 'Unavailable information stays clearly labeled.' }
      ]}
    />
  );
}
