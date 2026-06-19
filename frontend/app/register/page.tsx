import Link from 'next/link';
import { AppHeader } from '@/components/AppHeader';
import { RegisterForm } from '@/components/RegisterForm';
import { ContentBlock } from '@/components/ContentBlock';

export default function RegisterPage() {
  return (
    <main className="shell">
      <AppHeader title="Create account" />
      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">First week free · new member setup</p>
        <h2>Create your account and start reading the board.</h2>
        <p className="hero-copy">
          Set up your profile, choose the sport you care about most, and start your free week. We ask for the basics so the account can
          be personalized, access can be handled responsibly, and your subscription can be connected later through your billing provider.
        </p>
        <div className="hero-actions">
          <Link className="ghost-button large" href="/pricing" style={{ textDecoration: 'none' }}>Compare plans</Link>
          <Link className="ghost-button large" href="/login" style={{ textDecoration: 'none' }}>Already a member?</Link>
        </div>
      </section>
      <RegisterForm />
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
