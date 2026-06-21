import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Post-Game Review | InQsi',
  description: 'Review why a saved slip passed or failed after the games are final.',
  alternates: { canonical: '/post-game-review' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/post-game-review"
      eyebrow="Post-game review"
      title="Best way to review a slip after the games end"
      intro="A post-game review looks at what happened after the games are final. InQsi shows which legs were right, which leg failed, and whether earlier market warnings mattered."
      sections={[
        { title: 'Start with the final score', copy: 'The review waits until the games are final.' },
        { title: 'Check each leg', copy: 'The customer can see which legs were right and which missed.' },
        { title: 'Read the warning history', copy: 'InQsi shows whether the slip had a warning before lock-in.' },
        { title: 'Learn from the result', copy: 'The goal is to improve the next review, not hide the miss.' }
      ]}
      faqs={[
        { question: 'What is a post-game review?', answer: 'It is a review of why a saved slip passed or failed after the games are final.' },
        { question: 'Does InQsi show partial accuracy?', answer: 'Yes. A slip can show how many legs were correct even if the full slip missed.' }
      ]}
    />
  );
}
