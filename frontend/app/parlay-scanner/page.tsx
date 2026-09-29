import type { Metadata } from 'next';
import { AppHeader } from '@/components/AppHeader';
import { SlipScannerClient } from '@/components/SlipScannerClient';

export const metadata: Metadata = {
  title: 'Build My Slip',
  description: 'Build a 3-leg slip from verified live markets, compare sportsbook prices, and analyze the completed slip.',
  alternates: { canonical: '/parlay-scanner' }
};

export default function Page() {
  return (
    <main className="inqsi-shell slip-page">
      <AppHeader eyebrow="InQsi" title="Build My Slip" />
      <SlipScannerClient />
    </main>
  );
}
