import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: '3-Leg Parlay Guide | InQsi',
  description: 'Learn why InQsi caps builder output at three legs and how a 3-leg slip creates eight possible outcome paths.',
  alternates: { canonical: '/3-leg-parlay-guide' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/3-leg-parlay-guide"
      eyebrow="3-leg discipline"
      title="Why InQsi caps builds at 3 legs"
      intro="InQsi keeps parlay builds capped at 3 legs because the goal is discipline, not forcing a longshot slip. A 3-leg slip still has eight possible outcome paths, which gives enough structure to analyze without encouraging oversized builds."
      sections={[
        { title: '3 legs maximum', copy: 'InQsi does not build 4-leg, 5-leg, or larger parlays.' },
        { title: 'Eight outcome paths', copy: 'A 3-leg slip creates eight possible win/loss paths for review.' },
        { title: 'Cleaner review', copy: 'The smaller structure makes it easier to see the strongest legs and the weak spot.' },
        { title: 'No forced build', copy: 'If the market does not support the structure, InQsi should warn the customer.' }
      ]}
      faqs={[
        { question: 'Does InQsi build parlays with more than 3 legs?', answer: 'No. InQsi parlay builds are capped at 3 legs.' },
        { question: 'Why does InQsi cap builds at 3 legs?', answer: 'The cap keeps the product focused on review discipline instead of oversized slip construction.' }
      ]}
    />
  );
}
