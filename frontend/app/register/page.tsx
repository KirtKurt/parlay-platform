import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { RegisterForm } from '@/components/RegisterForm';
import { ContentBlock } from '@/components/ContentBlock';
import { SportIconStrip, TeamJerseyBadge } from '@/components/SportVisuals';

export default function RegisterPage() {
  return (
    <main className="shell auth-shell">
      <AppHeader title="Create account" />
      <section className="auth-top-grid register-top-grid">
        <RegisterForm />
        <div className="hero-card glass-card auth-side-card" style={{ minHeight: 0 }}>
          <p className="eyebrow blue">First week free · Pro selected</p>
          <h2>Create your account and start reading the board.</h2>
          <p className="hero-copy">
            Set up your profile, choose the sport you care about most, and start your free week with Pro selected by default.
          </p>
          <div className="team-badge-row" style={{ marginTop: 14 }}>
            <TeamJerseyBadge abbr="PRO" tone="gold" number="79" />
            <TeamJerseyBadge abbr="BOS" tone="green" number="0" />
            <TeamJerseyBadge abbr="LAL" tone="gold" number="23" />
          </div>
          <div className="hero-actions">
            <Link className="ghost-button large" href="/pricing" style={{ textDecoration: 'none' }}>Compare plans</Link>
            <Link className="ghost-button large" href="/login" style={{ textDecoration: 'none' }}>Already a member?</Link>
          </div>
        </div>
      </section>
      <SportIconStrip compact />
      <ContentBlock
        eyebrow="What happens after signup"
        title="Your preview turns into a real workspace"
        body="Once your account is created, the public preview becomes a more useful dashboard. You can come back to the sports you follow, see member-only board sections, and keep the workflow organized around the way you actually watch the market."
        items={[
          { title: 'Your sports come first', detail: 'Pick a primary sport so the first board feels relevant right away.' },
          { title: 'Free launch week', detail: 'Use the product for the first week before monthly access begins.' },
          { title: 'Clear acknowledgments', detail: 'Age, location, and informational-use acknowledgments keep access responsible.' },
          { title: 'Ready for billing later', detail: 'The flow is prepared for your provider-neutral monthly billing setup.' }
        ]}
      />
    </main>
  );
}
