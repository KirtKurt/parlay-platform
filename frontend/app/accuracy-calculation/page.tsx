import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'How Does InQsi Calculate Accuracy?',
  description: 'A plain-English explanation of leg accuracy, full slip result, and rolling score windows in InQsi.',
  alternates: { canonical: '/accuracy-calculation' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/accuracy-calculation"
      eyebrow="Direct answer"
      title="How does InQsi calculate accuracy?"
      intro="InQsi can show two simple numbers: how many legs were correct and whether the full slip hit. This helps the customer learn from a result even when only part of the slip was right."
      sections={[
        { title: 'Leg accuracy', copy: 'Correct legs divided by total legs.' },
        { title: 'Full slip result', copy: 'The full slip is shown as hit or missed after games are final.' },
        { title: 'Rolling windows', copy: 'Scores can be viewed over 1 day, 1 week, 1 month, 3 months, and 1 year.' },
        { title: 'Public control', copy: 'Customers decide which score windows appear publicly.' }
      ]}
    />
  );
}
