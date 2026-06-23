'use client';

import { FormEvent, useState } from 'react';
import { useRouter } from 'next/navigation';
import { createDemoMemberSession, saveMemberSession } from '@/lib/memberSession';
import { registrationSports, registrationStates } from '@/lib/subscription';

export function RegisterForm() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [status, setStatus] = useState<'idle' | 'created'>('idle');

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const submittedEmail = String(formData.get('email') ?? email).trim();
    saveMemberSession(createDemoMemberSession(submittedEmail, 'Full Access'));
    setStatus('created');

    window.setTimeout(() => {
      router.push('/account');
    }, 450);
  }

  return (
    <form className="panel" onSubmit={handleSubmit} style={{ display: 'grid', gap: 18 }}>
      <div className="panel-header compact">
        <div>
          <p className="eyebrow blue">New member</p>
          <h3>Create your InQsi account</h3>
          <p className="slip-note">Start the 5-day free promo and enter your workspace.</p>
        </div>
      </div>

      <div className="form-grid">
        <label className="field-card"><span>First name</span><input required name="firstName" placeholder="First name" /></label>
        <label className="field-card"><span>Last name</span><input required name="lastName" placeholder="Last name" /></label>
        <label className="field-card"><span>Email</span><input required name="email" type="email" placeholder="you@example.com" value={email} onChange={(event) => setEmail(event.target.value)} /></label>
        <label className="field-card"><span>Phone</span><input name="phone" type="tel" placeholder="Mobile number" /></label>
        <label className="field-card"><span>State</span><select required name="state"><option value="">Select state</option>{registrationStates.map((state) => <option key={state} value={state}>{state}</option>)}</select></label>
        <label className="field-card"><span>Primary sport</span><select required name="primarySport"><option value="">Select sport</option>{registrationSports.map((sport) => <option key={sport} value={sport}>{sport}</option>)}</select></label>
      </div>

      <button className="primary-button large" type="submit">Create account and enter workspace</button>
      {status === 'created' && <div className="compliance-box success-box">Account created. Opening your workspace now.</div>}
    </form>
  );
}
