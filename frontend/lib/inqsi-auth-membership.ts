export type InqsiAuthProvider = 'google' | 'apple' | 'email';

export type InqsiMembershipPlan = {
  id: string;
  name: string;
  trialDays: number;
  monthlyPriceCents: number;
  enabled: boolean;
};

export const INQSI_AUTH_PROVIDERS: Array<{ id: InqsiAuthProvider; label: string; envKey: string; enabled: boolean }> = [
  { id: 'google', label: 'Continue with Google', envKey: 'NEXT_PUBLIC_GOOGLE_CLIENT_ID', enabled: Boolean(process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID) },
  { id: 'apple', label: 'Continue with Apple', envKey: 'NEXT_PUBLIC_APPLE_CLIENT_ID', enabled: Boolean(process.env.NEXT_PUBLIC_APPLE_CLIENT_ID) },
  { id: 'email', label: 'Continue with email', envKey: 'EMAIL_AUTH_PROVIDER', enabled: Boolean(process.env.EMAIL_AUTH_PROVIDER) }
];

const processorReady = Boolean(process.env.MEMBER_PROCESSOR_API_KEY && process.env.NEXT_PUBLIC_MEMBER_PLAN_ID);

export const INQSI_MEMBERSHIP_PLANS: InqsiMembershipPlan[] = [
  { id: 'inqsi-premium-monthly', name: 'InQsi Premium', trialDays: 5, monthlyPriceCents: 3500, enabled: processorReady }
];

export function getAuthReadiness() {
  const ready = INQSI_AUTH_PROVIDERS.some((provider) => provider.enabled);
  return { providers: INQSI_AUTH_PROVIDERS, ready, message: ready ? 'Auth provider configured.' : 'Working on it. OAuth provider keys are not connected yet.' };
}

export function getMembershipReadiness() {
  const plan = INQSI_MEMBERSHIP_PLANS[0];
  return { plan, ready: plan.enabled, message: plan.enabled ? 'Member processor configured.' : 'Working on it. Member processor keys are not connected yet.' };
}
