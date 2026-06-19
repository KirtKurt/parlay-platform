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
        <h2>Create your account and unlock the sports market terminal.</h2>
        <p className="hero-copy">
          Register once to personalize your sports board, select your primary sport, confirm age and location, and begin the first free week of launch access. The signup flow is designed to collect only the information needed to support access, safety, and subscription readiness.
        </p>
        <div className="hero-actions">
          <Link className="ghost-button large" href="/pricing" style={{ textDecoration: 'none' }}>Compare plans</Link>
          <Link className="ghost-button large" href="/login" style={{ textDecoration: 'none' }}>Already a member?</Link>
        </div>
      </section>
      <RegisterForm />
      <ContentBlock
        eyebrow="Why registration matters"
        title="A cleaner account profile improves the member experience"
        body="Registration turns the public preview into a member workspace. The profile fields help route users toward the sports they care about, support age and location acknowledgments, and prepare the account for provider-neutral recurring access when payment credentials are supplied later."
        items={[
          { title: 'Personalized sports', detail: 'Primary sport selection helps shape the first dashboard experience.' },
          { title: 'Free launch week', detail: 'New members can explore the product before full monthly access begins.' },
          { title: 'Safety acknowledgments', detail: 'Age, location, and informational-use acknowledgments support responsible access.' },
          { title: 'Future-ready', detail: 'The flow is prepared for backend identity and billing confirmation.' }
        ]}
      />
    </main>
  );
}
