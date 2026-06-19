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
          <h2>Log in and get back to the board.</h2>
          <p className="hero-copy">Your account opens the sports board, pick-audit flow, saved sports, and free-week access. This working login is ready for site testing while production authentication is connected behind it.</p>
          <div className="hero-actions">
            <Link className="primary-button large" href="/register" style={{ textDecoration: 'none' }}>Create new account</Link>
            <Link className="ghost-button large" href="/pricing" style={{ textDecoration: 'none' }}>View plans</Link>
          </div>
        </div>
        <LoginForm />
      </section>
    </main>
  );
}
