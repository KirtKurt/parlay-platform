import type { Metadata } from 'next';
import { AppHeader } from '@/components/AppHeader';
import { SlipScannerClient } from '@/components/SlipScannerClient';

export const metadata: Metadata = {
  title: 'Slip Scanner — Find the Risk Before You Bet',
  description: 'Choose a sport, game, market and selection. InQsi reviews available sportsbook movement, price quality and qualified sports intelligence.',
  alternates: { canonical: '/parlay-scanner' }
};

export default function Page() {
  return (
    <main className="mockup-site">
      <AppHeader active="scanner" />
      <section className="scanner-approved">
        <header className="scanner-title">
          <span className="mockup-eyebrow">Slip Scanner</span>
          <h1>Find the <span>risk</span> before you bet.</h1>
          <p>Pick your sport. Pick your game. Pick your side. InQsi finds the risk.</p>
        </header>
        <SlipScannerClient />
      </section>
    </main>
  );
}
