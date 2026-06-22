'use client';

import { FormEvent, useState } from 'react';
import { useRouter } from 'next/navigation';
import { createDemoMemberSession, saveMemberSession } from '@/lib/memberSession';
import { defaultPlanId, registrationSports, registrationStates, type PlanId } from '@/lib/subscription';

export function RegisterForm() {
  const router = useRouter();
  const [planId] = useState<PlanId>(defaultPlanId);
  const [email, setEmail] = useState('');

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    saveMemberSession(createDemoMemberSession(email, 'Full Access'));
    const params = new URLSearchParams({ plan: planId });
    if (email) params.set('email', email);
    router.push(`/checkout?${params.toString()}`);
  }

  return (
    <form className="panel" onSubmit={handleSubmit} style={{ display: 'grid', gap: 18 }}>
      <div className="panel-header compact">
        <div>
          <p className="eyebrow blue">New member</p>
          <h3>Create your InQsi account</h3>
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

      <button className="primary-button large" type="submit">Continue</button>
    </form>
  );
}
