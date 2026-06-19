import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { LoginForm } from '@/components/LoginForm';
import { SportIconStrip, TeamJerseyBadge } from '@/components/SportVisuals';

export default function LoginPage() {
  return (
    <main className="shell auth-shell">
      <AppHeader title="Member login" />
      <section className="auth-top-grid">
        <LoginForm />
        <div className="hero-card glass-card auth-side-card" style={{ minHeight: 0 }}>
          <p className="eyebrow blue">Subscriber access</p>
          <h2>Get straight back to your board.</h2>
          <p className="hero-copy">Sign in to review your watchlist, saved boards, and Pro market workspace.</p>
          <div className="team-badge-row" style={{ marginTop: 14 }}>
            <TeamJerseyBadge abbr="PRO" tone="gold" number="79" />
            <TeamJerseyBadge abbr="BUF" tone="blue" number="17" />
            <TeamJerseyBadge abbr="MIA" tone="teal" number="10" />
          </div>
          <div className="hero-actions">
            <Link className="primary-button large" href="/register" style={{ textDecoration: 'none' }}>Create new account</Link>
            <Link className="ghost-button large" href="/pricing" style={{ textDecoration: 'none' }}>View plans</Link>
          </div>
        </div>
      </section>
      <SportIconStrip compact />
    </main>
  );
}
