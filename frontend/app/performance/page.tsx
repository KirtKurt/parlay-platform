import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Review History',
  description: 'Review saved slips, market reads, post-game analysis, and InQsi accuracy scores over time.',
  alternates: { canonical: '/performance' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/performance"
      eyebrow="Review history"
      title="Look back at what the market showed you."
      intro="Review History helps you revisit past slips, saved games, market reads, and post-game analysis. Use it to see what signals were present, what changed later, and how your accuracy score develops over time."
      sections={[
        { title: 'Past slips', copy: 'Return to the slips, boards, scanner results, and builder outputs you saved.' },
        { title: 'Post-game analysis', copy: 'After games are final, InQsi should score each saved slip and show what was right, what missed, and where the risk showed up.' },
        { title: 'Accuracy windows', copy: 'Review accuracy by individual parlay, 1 day, 1 week, 1 month, 3 months, and 1 year.' },
        { title: 'Public or private', copy: 'Keep slips private by default or choose which ones can be shown publicly. Comments remain off for now.' }
      ]}
    />
  );
}
