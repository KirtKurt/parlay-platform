export type PlanId = 'starter' | 'core' | 'pro';

export type SubscriptionPlan = {
  id: PlanId;
  name: string;
  price: string;
  interval: string;
  description: string;
  features: string[];
  cta: string;
};

export const subscriptionPlans: SubscriptionPlan[] = [
  {
    id: 'starter',
    name: 'Starter',
    price: '$19',
    interval: 'per month',
    description: 'Entry-level access for users who want the market board and limited game detail pages.',
    features: ['Daily market board', 'Limited game pages', 'Methodology library', 'Mock parlay preview'],
    cta: 'Start Starter'
  },
  {
    id: 'core',
    name: 'Core',
    price: '$35',
    interval: 'per month',
    description: 'The main Silvers Syndicate subscription for full sport boards and parlay risk intelligence.',
    features: ['Full sport pages', '15-minute line movement', 'Top-8 parlay ranking', 'Top-3 containment view', 'Steam/resistance/chaos signals'],
    cta: 'Start Core'
  },
  {
    id: 'pro',
    name: 'Pro',
    price: '$79',
    interval: 'per month',
    description: 'Advanced workflow for no-overlap builds, alerting, and deeper market anomaly review.',
    features: ['Everything in Core', 'No-overlap build workspace', 'Market Anomaly flags', 'Saved watchlists', 'Human-gate review notes'],
    cta: 'Start Pro'
  }
];

export const defaultPlanId: PlanId = 'core';

export function getPlan(planId?: string | null) {
  return subscriptionPlans.find((plan) => plan.id === planId) ?? subscriptionPlans.find((plan) => plan.id === defaultPlanId)!;
}

export const registrationSports = [
  'NFL',
  'CFB',
  'NBA',
  'NCAAM',
  'NHL',
  'MLB',
  'Tennis',
  'Soccer',
  'Darts',
  'Lacrosse',
  'Table Tennis'
];

export const registrationStates = [
  'Alabama', 'Alaska', 'Arizona', 'Arkansas', 'California', 'Colorado', 'Connecticut', 'Delaware',
  'Florida', 'Georgia', 'Hawaii', 'Idaho', 'Illinois', 'Indiana', 'Iowa', 'Kansas', 'Kentucky',
  'Louisiana', 'Maine', 'Maryland', 'Massachusetts', 'Michigan', 'Minnesota', 'Mississippi',
  'Missouri', 'Montana', 'Nebraska', 'Nevada', 'New Hampshire', 'New Jersey', 'New Mexico',
  'New York', 'North Carolina', 'North Dakota', 'Ohio', 'Oklahoma', 'Oregon', 'Pennsylvania',
  'Rhode Island', 'South Carolina', 'South Dakota', 'Tennessee', 'Texas', 'Utah', 'Vermont',
  'Virginia', 'Washington', 'West Virginia', 'Wisconsin', 'Wyoming'
];
