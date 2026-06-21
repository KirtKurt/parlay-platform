export type InqsiPlanId = 'free_preview' | 'premium';
export type InqsiAccessStatus = 'anonymous' | 'trial' | 'active' | 'past_due' | 'canceled';

export type InqsiAccountAccess = {
  userId?: string;
  plan: InqsiPlanId;
  status: InqsiAccessStatus;
  trialStartedAt?: string;
  trialDays: number;
};

export function getTrialEndsAt(trialStartedAt: string, trialDays = 5) {
  const start = new Date(trialStartedAt);
  start.setDate(start.getDate() + trialDays);
  return start.toISOString();
}

export function getAccessLevel(access: InqsiAccountAccess) {
  if (access.status === 'active') return { allowed: true, tier: 'premium', message: 'Premium access active.' };
  if (access.status === 'trial' && access.trialStartedAt) {
    const trialEndsAt = getTrialEndsAt(access.trialStartedAt, access.trialDays);
    const active = new Date(trialEndsAt).getTime() > Date.now();
    return { allowed: active, tier: active ? 'trial' : 'expired', trialEndsAt, message: active ? 'Trial access active.' : 'Trial expired.' };
  }
  return { allowed: false, tier: 'preview', message: 'Create an account or subscribe to unlock premium workspace.' };
}

export const ACCESS_READINESS = {
  authReady: Boolean(process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || process.env.NEXT_PUBLIC_APPLE_CLIENT_ID || process.env.EMAIL_AUTH_PROVIDER),
  billingReady: Boolean(process.env.STRIPE_SECRET_KEY && process.env.NEXT_PUBLIC_STRIPE_PRICE_ID),
  trialDays: 5,
  status: process.env.STRIPE_SECRET_KEY && process.env.NEXT_PUBLIC_STRIPE_PRICE_ID ? 'ready' : 'working_on_it'
};
