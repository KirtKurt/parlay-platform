export type PlanId = 'core' | 'pro';

export type SubscriptionPlan = {
  id: PlanId;
  name: string;
  price: string;
  interval: string;
  description: string;
  features: string[];
  cta: string;
};

export type FeatureComparisonRow = {
  feature: string;
  core: string;
  pro: string;
};

export const subscriptionPlans: SubscriptionPlan[] = [
  {
    id: 'core',
    name: 'Core',
    price: '$35',
    interval: 'per month',
    description: 'The main Silvers Syndicate subscription for daily sports market intelligence, full sport boards, line movement, and parlay risk structure.',
    features: [
      'First week free for new launch members',
      'Full sport pages across NFL, CFB, NBA, NCAAM, NHL, MLB, tennis, soccer, darts, lacrosse, and table tennis',
      'Game and match market boards with moneyline, spread, total, and signal context where available',
      '15-minute line movement preview with T1/T2/T3 snapshot framing',
      'Steam, resistance, coin-flip, chaos, and market-anomaly labels',
      'Top-8 parlay ranking view for supported slates',
      'Top-3 containment view and core risk notes',
      'Methodology library explaining T-snapshots, line movement, and risk classification'
    ],
    cta: 'Start Core'
  },
  {
    id: 'pro',
    name: 'Pro',
    price: '$79',
    interval: 'per month',
    description: 'Advanced workflow for serious slate review, no-overlap builds, saved research, and deeper market-anomaly monitoring.',
    features: [
      'Everything included in Core',
      'No-overlap parlay build workspace for multi-card construction',
      'Deeper market anomaly review and escalation notes',
      'Saved watchlists for games, teams, matches, and high-volatility slates',
      'Human-gate review notes and safer-leg substitution indicators when available',
      'Advanced slate filtering by sport, signal, confidence, and structure',
      'Priority access to new sport modules and beta data views',
      'Expanded methodology notes for advanced users and recurring slate review'
    ],
    cta: 'Start Pro'
  }
];

export const featureComparison: FeatureComparisonRow[] = [
  { feature: 'First week free', core: 'Included', pro: 'Included' },
  { feature: 'Sports covered', core: 'All public sport boards', pro: 'All public sport boards + beta modules' },
  { feature: 'Line movement', core: '15-minute movement view', pro: '15-minute movement view + deeper review context' },
  { feature: 'T-snapshot framework', core: 'T1/T2/T3 framing', pro: 'T1/T2/T3 framing + advanced review notes' },
  { feature: 'Signals', core: 'Steam, resistance, coin flip, chaos, anomaly labels', pro: 'Signals + anomaly escalation notes' },
  { feature: 'Parlay rankings', core: 'Top-8 ranking and Top-3 containment view', pro: 'Top-8 ranking + advanced build workspace' },
  { feature: 'No-overlap builds', core: 'Limited preview', pro: 'Included' },
  { feature: 'Watchlists', core: 'Not included', pro: 'Included' },
  { feature: 'Human-gate review notes', core: 'Not included', pro: 'Included when available' },
  { feature: 'Best for', core: 'Daily slate research and core market intelligence', pro: 'Power users building multiple cards and tracking volatility' }
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
