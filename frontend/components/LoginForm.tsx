'use client';

import { FormEvent, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { signIn } from 'next-auth/react';
import { createDemoMemberSession, saveMemberSession } from '@/lib/memberSession';

export function LoginForm() {
  const router = useRouter();
  const [status, setStatus] = useState<'idle' | 'signed-in' | 'oauth'>('idle');
  const [email, setEmail] = useState('');
  const [googleReady, setGoogleReady] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setEmail(params.get('email') ?? '');
    fetch('/api/auth/providers').then((r) => r.ok ? r.json() : {}).then((providers) => {
      setGoogleReady(Boolean(providers?.google));
    }).catch(() => setGoogleReady(false));
  }, []);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const submittedEmail = String(formData.get('email') ?? email).trim();
    saveMemberSession(createDemoMemberSession(submittedEmail, 'Full Access'));
    setStatus('signed-in');
    window.setTimeout(() => router.push('/account'), 450);
  }

  return (
    <form className="panel login-panel-sticky" onSubmit={handleSubmit} style={{ display: 'grid', gap: 18 }}>
      <div>
        <p className="eyebrow blue">Member login</p>
        <h3>Sign in to your market workspace</h3>
        <p className="slip-note">Google creates one InQsi identity. Email sign-in is a local workspace fallback only.</p>
      </div>
      <button
        className="primary-button large"
        type="button"
        disabled={!googleReady}
        onClick={() => {
          setStatus('oauth');
          signIn('google', { callbackUrl: '/arbitrage-v2' });
        }}
      >
        {googleReady ? 'Continue with Google' : 'Google sign-in waiting on provider credentials'}
      </button>
      <label className="field-card full-span">
        <span>Email fallback</span>
        <input required name="email" type="email" placeholder="you@example.com" value={email} onChange={(event) => setEmail(event.target.value)} />
      </label>
      <button className="ghost-button large" type="submit">Sign in with email</button>
      {status === 'signed-in' && (
        <div className="compliance-box success-box">Workspace opened. Sending you to your account.</div>
      )}
      {status === 'oauth' && (
        <div className="compliance-box">Redirecting to Google.</div>
      )}
    </form>
  );
}
