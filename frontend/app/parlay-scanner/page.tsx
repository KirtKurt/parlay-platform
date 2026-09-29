import type { Metadata } from 'next';
import { AppHeader } from '@/components/AppHeader';
import { SlipScannerClient } from '@/components/SlipScannerClient';

export const metadata: Metadata = {
  title: 'Slip Scanner — Find Bet Risk',
  description: 'Choose your sport, event and selection. InQsi analyzes market movement, price quality and available risk intelligence before you bet.',
  alternates: { canonical: '/parlay-scanner' }
};

export default function Page() {
  return (
    <main className="inqsi-shell slip-page">
      <AppHeader eyebrow="InQsi" title="Slip Scanner" />
      <SlipScannerClient />
    </main>
  );
}
