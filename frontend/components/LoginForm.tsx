'use client';

import { FormEvent, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { OAuthButtons } from '@/components/OAuthButtons';
import { createDemoMemberSession, saveMemberSession } from '@/lib/memberSession';

export function LoginForm() {
  const router = useRouter();
  const [status, setStatus] = useState<'idle' | 'signed-in'>('idle');
  const [email, setEmail] = useState('');

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setEmail(params.get('email') ?? '');
  }, []);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const submittedEmail = String(formData.get('email') ?? email).trim();
    saveMemberSession(createDemoMemberSession(submittedEmail, 'Full Access'));
    setStatus('signed-in');
    window.setTimeout(() => {
      router.push('/account');
    }, 450);
  }

  return (
    <form className="panel login-panel-sticky" onSubmit={handleSubmit} style={{ display: 'grid', gap: 18 }}>
      <div>
        <p className="eyebrow blue">Member login</p>
        <h3>Sign in to your market workspace</h3>
        <p className="slip-note">Continue with Google if it is enabled, or use email to open the workspace.</p>
      </div>
      <OAuthButtons />
      <label className="field-card full-span">
        <span>Email</span>
        <input required name="email" type="email" placeholder="you@example.com" value={email} onChange={(event) => setEmail(event.target.value)} />
      </label>
      <button className="primary-button large" type="submit">Sign in with email</button>
      {status === 'signed-in' && (
        <div className="compliance-box success-box">
          You are signed in. Sending you to your account workspace now.
        </div>
      )}
    </form>
  );
}
