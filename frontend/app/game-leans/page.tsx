import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Game Leans',
  description: 'Review InQsi game leans with market context, signal strength, and risk notes before lock-in.',
  alternates: { canonical: '/game-leans' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/game-leans"
      eyebrow="Game leans"
      title="Game leans with market context."
      intro="InQsi does not ask you to blindly trust a pick. Game leans show where the market appears to be pointing, what signals support the read, and what warning signs still deserve attention."
      sections={[
        { title: 'Market direction', copy: 'See which side appears to have market support when the board has enough information to review.' },
        { title: 'Signal context', copy: 'Each lean is supported by market movement, stability, and a short what-to-watch explanation.' },
        { title: 'Risk notes', copy: 'Resistance, chaos, and coin-flip pressure stay visible so a lean does not feel stronger than it should.' },
        { title: 'No guarantees', copy: 'InQsi gives you a market read, not certainty. The goal is to help you make a calmer decision before lock-in.' }
      ]}
      faqs={[
        { question: 'Does InQsi guarantee an outcome?', answer: 'No. InQsi presents market-based leans and risk context, not guarantees.' },
        { question: 'What should I look for first?', answer: 'Start with the signal context, then check whether resistance or instability is warning you to slow down.' },
        { question: 'What happens if data is missing?', answer: 'InQsi uses a clear waiting state rather than displaying artificial results.' }
      ]}
    />
  );
}
