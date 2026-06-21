import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Accuracy Tracker | InQsi',
  description: 'Track slip accuracy by individual review, 1 day, 1 week, 1 month, 3 months, and 1 year.',
  alternates: { canonical: '/accuracy-tracker' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/accuracy-tracker"
      eyebrow="Accuracy tracker"
      title="How to track slip accuracy"
      intro="InQsi tracks accuracy in a simple way: how many legs were right, whether the full slip hit, and how the customer's score changes over time."
      sections={[
        { title: 'Leg accuracy', copy: 'A 3-leg slip can show 2 of 3 legs correct even if the full slip missed.' },
        { title: 'Full slip result', copy: 'The full slip can still be marked hit or missed.' },
        { title: 'Rolling windows', copy: 'Customers can review 1 day, 1 week, 1 month, 3 months, and 1 year views.' },
        { title: 'Public score control', copy: 'Customers decide whether 1-week or 1-month scores appear publicly.' }
      ]}
      faqs={[
        { question: 'How does InQsi calculate accuracy?', answer: 'InQsi can show leg accuracy as correct legs divided by total legs and also show whether the full slip hit.' },
        { question: 'Can scores stay private?', answer: 'Yes. Public score display is controlled by the customer.' }
      ]}
    />
  );
}
