import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Game Leans Dashboard',
  description: 'Review InQsi game lean context, market stability, signal strength, and data availability.',
  alternates: { canonical: '/game-leans' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/game-leans"
      eyebrow="Game leans"
      title="Game leans with market context."
      intro="InQsi presents game leans with signal score context, market stability, and short explanations. If verified data is unavailable, this page shows Working on it."
      sections={[
        { title: 'Market-aware visibility', copy: 'Game leans are designed to appear when the market has enough information to review.' },
        { title: 'Signal context', copy: 'Each lean is supported by market movement, stability, and a short what-to-watch explanation.' },
        { title: 'Responsible language', copy: 'InQsi avoids guarantee language and keeps the message clear and professional.' },
        { title: 'Sport-specific learning', copy: 'Results are stored by sport so each sport improves independently.' }
      ]}
      faqs={[
        { question: 'Does InQsi guarantee an outcome?', answer: 'No. InQsi presents market-based leans and context, not guarantees.' },
        { question: 'When should game leans appear?', answer: 'The product goal is to show leans when verified data is available and the market has enough context.' },
        { question: 'What happens if data is missing?', answer: 'InQsi shows Working on it rather than displaying artificial results.' }
      ]}
    />
  );
}
