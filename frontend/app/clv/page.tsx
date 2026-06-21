import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

export const metadata: Metadata = {
  title: 'Line Movement Review',
  description: 'Review how market lines move after your first InQsi read.',
  alternates: { canonical: '/clv' }
};

export default function Page() {
  return (
    <InqsiSeoPage
      path="/clv"
      eyebrow="Line movement"
      title="Show how the number moved after your first read."
      intro="This page keeps suggestion 5 visible for customers: a simple line movement review. It helps you look back at where the market was when you first checked a game and how the number moved later."
      sections={[
        { title: 'First read', copy: 'Save the market position from your first review.' },
        { title: 'Later movement', copy: 'Compare the first read with where the market moved later.' },
        { title: 'Signal timing', copy: 'Review whether the market gave useful clues early or late.' },
        { title: 'Simple language', copy: 'InQsi shows this as line movement review instead of technical CLV language.' }
      ]}
    />
  );
}
