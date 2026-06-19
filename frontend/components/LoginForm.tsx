'use client';

import { FormEvent, useState } from 'react';

export function LoginForm() {
  const [status, setStatus] = useState<'idle' | 'demo'>('idle');

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStatus('demo');
  }

  return (
    <form className="panel" onSubmit={handleSubmit} style={{ display: 'grid', gap: 18 }}>
      <div>
        <p className="eyebrow blue">Member login</p>
        <h3>Sign in to your market workspace</h3>
      </div>
      <label className="field-card full-span">
        <span>Email</span>
        <input required type="email" placeholder="you@example.com" />
      </label>
      <label className="field-card full-span">
        <span>Password</span>
        <input required type="password" placeholder="Password" />
      </label>
      <button className="primary-button large" type="submit">Sign in</button>
      {status === 'demo' && (
        <div className="compliance-box">
          Login UI is in place. Live authentication should connect to AWS Cognito before launch.
        </div>
      )}
    </form>
  );
}
