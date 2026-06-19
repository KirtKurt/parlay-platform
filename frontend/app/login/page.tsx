import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { LoginForm } from '@/components/LoginForm';

export default function LoginPage() {
  return (
    <main className="shell">
      <AppHeader title="Member login" />
      <section className="hero-grid">
        <div className="hero-card glass-card" style={{ minHeight: 0 }}>
          <p className="eyebrow blue">Subscriber access</p>
          <h2>Return to your watchlist, sport boards, and parlay build history.</h2>
          <p className="hero-copy">Authentication UI is staged now. Production login should connect to AWS Cognito so member access can be controlled by subscription status.</p>
          <Link className="ghost-button large" href="/register" style={{ textDecoration: 'none' }}>Create new account</Link>
        </div>
        <LoginForm />
      </section>
    </main>
  );
}
