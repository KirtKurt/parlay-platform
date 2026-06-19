export type AccessRole = 'VISITOR' | 'REGISTERED' | 'SUBSCRIBER' | 'MASTER';

export type SubscriptionStatus = 'NONE' | 'PENDING' | 'ACTIVE' | 'PAST_DUE' | 'CANCELLED' | 'MASTER_BYPASS';

export type ViewerAccess = {
  role: AccessRole;
  subscriptionStatus: SubscriptionStatus;
  canViewTeaser: boolean;
  canViewPremium: boolean;
  canBuildParlays: boolean;
  canUseAdminTools: boolean;
  label: string;
};

export const VISITOR_ACCESS: ViewerAccess = {
  role: 'VISITOR',
  subscriptionStatus: 'NONE',
  canViewTeaser: true,
  canViewPremium: false,
  canBuildParlays: false,
  canUseAdminTools: false,
  label: 'Visitor preview'
};

export const REGISTERED_ACCESS: ViewerAccess = {
  role: 'REGISTERED',
  subscriptionStatus: 'PENDING',
  canViewTeaser: true,
  canViewPremium: false,
  canBuildParlays: false,
  canUseAdminTools: false,
  label: 'Registered preview'
};

export const SUBSCRIBER_ACCESS: ViewerAccess = {
  role: 'SUBSCRIBER',
  subscriptionStatus: 'ACTIVE',
  canViewTeaser: true,
  canViewPremium: true,
  canBuildParlays: true,
  canUseAdminTools: false,
  label: 'Active subscriber'
};

export const MASTER_ACCESS: ViewerAccess = {
  role: 'MASTER',
  subscriptionStatus: 'MASTER_BYPASS',
  canViewTeaser: true,
  canViewPremium: true,
  canBuildParlays: true,
  canUseAdminTools: true,
  label: 'Master access'
};

export function resolveAccess(role?: AccessRole, subscriptionStatus?: SubscriptionStatus): ViewerAccess {
  if (role === 'MASTER') return MASTER_ACCESS;
  if (role === 'SUBSCRIBER' && subscriptionStatus === 'ACTIVE') return SUBSCRIBER_ACCESS;
  if (role === 'REGISTERED') return REGISTERED_ACCESS;
  return VISITOR_ACCESS;
}

export const premiumLockedMessage = 'Create an account or sign in to unlock full line movement, Top-8 rankings, parlay build outputs, and game-level signal explanations.';
