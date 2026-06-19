import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { LoginForm } from '@/components/LoginForm';

export default function LoginPage() {
  return (
    <main className="shell auth-shell">
      <AppHeader title="Member login" />
      <section className="auth-top-grid">
        <LoginForm />
        <div className="hero-card glass-card auth-side-card" style={{ minHeight: 0 }}>
          <p className="eyebrow blue">Subscriber access</p>
          <h2>Get straight back to your board.</h2>
          <p className="hero-copy">Sign in to review your watchlist, saved boards, pick-audit flow, and Pro market workspace.</p>
          <div className="hero-actions">
            <Link className="primary-button large" href="/register" style={{ textDecoration: 'none' }}>Create new account</Link>
            <Link className="ghost-button large" href="/pricing" style={{ textDecoration: 'none' }}>View plans</Link>
          </div>
        </div>
      </section>
    </main>
  );
}
