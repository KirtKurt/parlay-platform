'use client';

import { FormEvent, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
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

    saveMemberSession(createDemoMemberSession(submittedEmail, 'Pro'));
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
        <p className="slip-note">Your InQsi workspace opens your watchlist, sport boards, AI Bet Slip Scanner, and member tools.</p>
      </div>
      <label className="field-card full-span">
        <span>Email</span>
        <input required name="email" type="email" placeholder="you@example.com" value={email} onChange={(event) => setEmail(event.target.value)} />
      </label>
      <label className="field-card full-span">
        <span>Password</span>
        <input required name="password" type="password" placeholder="Password" />
      </label>
      <label className="field-card full-span">
        <span>Plan access</span>
        <select name="plan" defaultValue="full_access">
          <option value="full_access">InQsi Full Access</option>
        </select>
      </label>
      <button className="primary-button large" type="submit">Sign in</button>
      {status === 'signed-in' && (
        <div className="compliance-box success-box">
          You are signed in with InQsi Full Access. Sending you to your account workspace now.
        </div>
      )}
      <div className="compliance-box">
        First 5 days free for new members. Preview your board, save a watchlist, and decide from there.
      </div>
    </form>
  );
}
