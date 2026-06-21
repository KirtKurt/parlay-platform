export type PlanId = 'full_access';

export type SubscriptionPlan = {
  id: PlanId;
  name: string;
  price: string;
  interval: string;
  monthlyPriceCents: number;
  description: string;
  features: string[];
  cta: string;
};

export type FeatureComparisonRow = {
  feature: string;
  fullAccess: string;
};

export const subscriptionPlans: SubscriptionPlan[] = [
  {
    id: 'full_access',
    name: 'InQsi Full Access',
    price: '$38',
    interval: 'per month',
    monthlyPriceCents: 3800,
    description: 'One InQsi membership package with all available sports market intelligence features included.',
    features: [
      '5-day free promo for new members',
      'All supported sport boards',
      'Game and match market boards with moneyline, spread, total, and signal context where available',
      '15-minute market movement view with T1/T2/T3 snapshot framing',
      'Steam, resistance, coin-flip, chaos, and market-anomaly labels',
      'Predicted winner lean when verified data supports it',
      'Best available line display when provider data supports it',
      'Top-8 ranking view for supported 3-leg structures',
      'Watchlists, alerts, saved research, and dashboard history',
      'Creator attribution and member access support'
    ],
    cta: 'Start Full Access'
  }
];

export const featureComparison: FeatureComparisonRow[] = [
  { feature: '5-day promo', fullAccess: 'Included' },
  { feature: 'Sports covered', fullAccess: 'All supported sport boards' },
  { feature: 'Line movement', fullAccess: '15-minute movement view' },
  { feature: 'T-snapshot framework', fullAccess: 'T1/T2/T3 framing' },
  { feature: 'Signals', fullAccess: 'Steam, resistance, coin flip, chaos, anomaly labels' },
  { feature: 'Parlay rankings', fullAccess: 'Top-8 ranking for supported 3-leg structures' },
  { feature: 'Watchlists', fullAccess: 'Included' },
  { feature: 'Alerts', fullAccess: 'Included' },
  { feature: 'Performance dashboard', fullAccess: 'Included when verified records exist' },
  { feature: 'Best for', fullAccess: 'One simple membership for all InQsi features' }
];

export const defaultPlanId: PlanId = 'full_access';

export function getPlan(planId?: string | null) {
  return subscriptionPlans.find((plan) => plan.id === planId) ?? subscriptionPlans[0];
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
