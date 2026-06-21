import type { Metadata } from 'next';
import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { ContentBlock } from '@/components/ContentBlock';
import { SportHeroPanel, SportIconStrip, TeamJerseyBadge } from '@/components/SportVisuals';

export const metadata: Metadata = {
  title: 'Start Here',
  description: 'Start with a simple path through InQsi: scan your picks, preview the sports board, compare access, and start 5 days free.',
  alternates: { canonical: '/start-here' },
  openGraph: {
    title: 'Start Here | InQsi',
    description: 'A simple first-time visitor path for scanning picks, previewing sports market movement, and starting 5 days free.'
  }
};

const steps = [
  {
    title: '1. Bring the pick you already like',
    detail: 'Start with the side, team, or parlay leg you were already thinking about. InQsi is built to challenge the ticket before you build it.'
  },
  {
    title: '2. Run it through the AI Bet Slip Scanner',
    detail: 'Look for resistance, late movement, reversal risk, and weak-leg exposure. The goal is not to talk you into more action. The goal is to show where your picks go wrong.'
  },
  {
    title: '3. Preview the sports board',
    detail: 'Choose a sport and see how the slate is organized. Each sport has its own equipment icon, board, signals, and market story.'
  },
  {
    title: '4. Unlock the full view when it is worth it',
    detail: 'Start with 5 days free. Use the scanner, review the board, and decide whether the full InQsi workspace is worth keeping.'
  }
];

export default function StartHerePage() {
  return (
    <main className="shell">
      <AppHeader eyebrow="Start Here" title="Use the market before you trust the pick" />

      <section className="sport-hero-grid">
        <div className="hero-card glass-card" style={{ minHeight: 0 }}>
          <p className="eyebrow blue">New here · 5 days free</p>
          <h2>Don’t start with a subscription. Start with a question.</h2>
          <p className="hero-copy">
            Take a pick you already like and ask why it might fail. If the market agrees, great. If it does not, you will see where the pressure is showing up before you lock anything in.
          </p>
          <div className="team-badge-row" style={{ marginTop: 16 }}>
            <TeamJerseyBadge abbr="BUF" tone="blue" number="17" />
            <b>vs</b>
            <TeamJerseyBadge abbr="MIA" tone="teal" number="10" />
            <span style={{ color: '#96a4bd', fontSize: '.85rem' }}>Example pick audit marker</span>
          </div>
          <div className="hero-actions">
            <Link className="primary-button large" href="/parlay-scanner" style={{ textDecoration: 'none' }}>AI Bet Slip Scanner</Link>
            <Link className="ghost-button large" href="/sports" style={{ textDecoration: 'none' }}>Preview Sports</Link>
            <Link className="ghost-button large" href="/pricing" style={{ textDecoration: 'none' }}>View Full Access</Link>
          </div>
        </div>
        <SportHeroPanel sportSlug="nfl" title="Start with one sport, then expand." copy="Every page now uses equipment icons for sport identity and jersey-style badges for team identity, without official team or league marks." />
      </section>

      <SportIconStrip compact />

      <ContentBlock
        eyebrow="Recommended path"
        title="A cleaner first visit"
        body="The website is now mapped around how a real person thinks: first doubt the pick, then preview the board, then understand the method, then decide whether 5 days free is worth using."
        items={steps}
      />

      <section className="panel" style={{ marginTop: 20 }}>
        <div className="panel-header">
          <div>
            <p className="eyebrow">Best next move</p>
            <h3>Start with the AI Bet Slip Scanner</h3>
          </div>
          <Link className="primary-button" href="/register?promo=5-days" style={{ textDecoration: 'none' }}>Start 5 Days Free</Link>
        </div>
        <p className="hero-copy" style={{ marginTop: 8 }}>
          The scanner is the strongest entry point because it meets users where they already are: they have a pick, they feel good about it, and they want to know whether the market is quietly warning them off.
        </p>
      </section>
    </main>
  );
}
