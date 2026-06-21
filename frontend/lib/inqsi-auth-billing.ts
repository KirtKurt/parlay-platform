export type InqsiAuthProvider = 'google' | 'apple' | 'email';

export type InqsiSubscriptionPlan = {
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

export const INQSI_SUBSCRIPTION_PLANS: InqsiSubscriptionPlan[] = [
  {
    id: 'inqsi-premium-monthly',
    name: 'InQsi Premium',
    trialDays: 5,
    monthlyPriceCents: 3500,
    enabled: Boolean(process.env.STRIPE_SECRET_KEY && process.env.NEXT_PUBLIC_STRIPE_PRICE_ID)
  }
];

export function getAuthReadiness() {
  return {
    providers: INQSI_AUTH_PROVIDERS,
    ready: INQSI_AUTH_PROVIDERS.some((provider) => provider.enabled),
    message: INQSI_AUTH_PROVIDERS.some((provider) => provider.enabled)
      ? 'Auth provider configured.'
      : 'Working on it. OAuth provider keys are not connected yet.'
  };
}

export function getSubscriptionReadiness() {
  const plan = INQSI_SUBSCRIPTION_PLANS[0];
  return {
    plan,
    ready: plan.enabled,
    message: plan.enabled ? 'Subscription plan configured.' : 'Working on it. Billing keys are not connected yet.'
  };
}
