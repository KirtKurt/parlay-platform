export type MemberStatus = 'anonymous' | 'trial' | 'live_paid' | 'past_due' | 'canceled' | 'expired' | 'refunded';

export const FULL_ACCESS_PLAN = {
  id: 'full_access',
  name: 'InQsi Full Access',
  monthlyPriceCents: 3800,
  priceLabel: '$38/month',
  trialDays: 5,
  includes: [
    'All supported sport boards',
    'Market movement and signal context',
    'Watchlists and alerts',
    'Best-line display when available',
    'Supported 3-leg ranking tools',
    'Dashboard history when verified records exist'
  ]
};

export function canUseFullAccess(status: MemberStatus) {
  return status === 'trial' || status === 'live_paid';
}

export function gateMessage(status: MemberStatus) {
  if (status === 'anonymous') return 'Create an account to start the 5-day free promo.';
  if (status === 'trial') return 'Trial access active.';
  if (status === 'live_paid') return 'Full access active.';
  if (status === 'past_due') return 'Account needs attention before full access continues.';
  if (status === 'canceled') return 'Membership canceled. Restart full access to continue.';
  if (status === 'refunded') return 'Membership refunded. Full access is disabled.';
  return 'Membership expired. Restart full access to continue.';
}

export function gateForStatus(status: MemberStatus) {
  return {
    allowed: canUseFullAccess(status),
    status,
    plan: FULL_ACCESS_PLAN,
    message: gateMessage(status)
  };
}
