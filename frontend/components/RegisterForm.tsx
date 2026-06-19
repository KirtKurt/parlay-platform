'use client';

import { FormEvent, useState } from 'react';
import { useRouter } from 'next/navigation';
import { createDemoMemberSession, saveMemberSession } from '@/lib/memberSession';
import { defaultPlanId, registrationSports, registrationStates, subscriptionPlans } from '@/lib/subscription';

export function RegisterForm() {
  const router = useRouter();
  const [planId, setPlanId] = useState(defaultPlanId);
  const [email, setEmail] = useState('');

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const plan = subscriptionPlans.find((item) => item.id === planId);
    saveMemberSession(createDemoMemberSession(email, plan?.name === 'Pro' ? 'Pro' : 'Core'));

    const params = new URLSearchParams({ plan: planId });
    if (email) params.set('email', email);
    router.push(`/checkout?${params.toString()}`);
  }

  return (
    <form className="panel" onSubmit={handleSubmit} style={{ display: 'grid', gap: 18 }}>
      <div className="panel-header compact">
        <div>
          <p className="eyebrow blue">New customer registration</p>
          <h3>Create your Silvers Syndicate account</h3>
        </div>
      </div>

      <div className="form-grid">
        <label className="field-card">
          <span>First name</span>
          <input required name="firstName" placeholder="First name" />
        </label>
        <label className="field-card">
          <span>Last name</span>
          <input required name="lastName" placeholder="Last name" />
        </label>
        <label className="field-card">
          <span>Email</span>
          <input required name="email" type="email" placeholder="you@example.com" value={email} onChange={(event) => setEmail(event.target.value)} />
        </label>
        <label className="field-card">
          <span>Phone</span>
          <input name="phone" type="tel" placeholder="Mobile number" />
        </label>
        <label className="field-card">
          <span>Date of birth</span>
          <input required name="birthDate" type="date" />
        </label>
        <label className="field-card">
          <span>State of residence</span>
          <select required name="state">
            <option value="">Select state</option>
            {registrationStates.map((state) => <option key={state} value={state}>{state}</option>)}
          </select>
        </label>
        <label className="field-card">
          <span>Primary sport</span>
          <select required name="primarySport">
            <option value="">Select sport</option>
            {registrationSports.map((sport) => <option key={sport} value={sport}>{sport}</option>)}
          </select>
        </label>
        <label className="field-card">
          <span>Use case</span>
          <select required name="useCase">
            <option value="">Select use case</option>
            <option>Personal research</option>
            <option>Parlay risk screening</option>
            <option>Market movement monitoring</option>
            <option>Syndicate/internal workflow</option>
          </select>
        </label>
      </div>

      <div className="plan-select-grid">
        {subscriptionPlans.map((plan) => (
          <label className={`plan-select-card ${plan.id === planId ? 'selected' : ''}`} key={plan.id}>
            <input type="radio" name="plan" value={plan.id} checked={plan.id === planId} onChange={() => setPlanId(plan.id)} />
            <span>{plan.name}</span>
            <strong>{plan.price}<small>/{plan.interval.replace('per ', '')}</small></strong>
            <p>{plan.description}</p>
          </label>
        ))}
      </div>

      <div className="compliance-box">
        <label><input required type="checkbox" name="ageGate" /> I confirm I am 21 or older.</label>
        <label><input required type="checkbox" name="terms" /> I understand Silvers Syndicate provides market intelligence and risk analysis, not guaranteed outcomes.</label>
        <label><input required type="checkbox" name="paymentConsent" /> I agree that payment will be handled through a secure hosted checkout provider; card numbers are not stored by Silvers Syndicate.</label>
      </div>

      <button className="primary-button large" type="submit">Continue to payment</button>
    </form>
  );
}
