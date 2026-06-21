export type InqsiLaunchItem = {
  id: string;
  area: string;
  title: string;
  status: 'ready' | 'working_on_it' | 'blocked' | 'needs_review';
  owner: 'product' | 'engineering' | 'legal' | 'marketing' | 'operations';
  note: string;
};

export const INQSI_LAUNCH_CHECKLIST: InqsiLaunchItem[] = [
  { id: 'frontend-design', area: 'Frontend', title: 'Mockup-matched InQsi layout', status: 'working_on_it', owner: 'engineering', note: 'Design system is implemented; final pixel-pass against approved mockup still needed.' },
  { id: 'all-pages', area: 'Frontend', title: 'All pages use InQsi design system', status: 'working_on_it', owner: 'engineering', note: 'Core pages are migrated; final route-by-route polish still needed.' },
  { id: 'data-providers', area: 'Data', title: 'Odds and score providers connected', status: 'blocked', owner: 'engineering', note: 'Provider keys must be connected before verified data can appear.' },
  { id: 'snapshots', area: 'Data', title: 'Snapshot storage active', status: 'blocked', owner: 'engineering', note: 'DynamoDB table name and write path must be connected.' },
  { id: 'signals', area: 'Intelligence', title: 'Signal engine connected to live snapshots', status: 'working_on_it', owner: 'engineering', note: 'Signal scaffold exists; production scoring requires real historical snapshots.' },
  { id: 'auth', area: 'Accounts', title: 'Login and signup providers connected', status: 'blocked', owner: 'engineering', note: 'Google, Apple, and email provider keys are required.' },
  { id: 'billing', area: 'Subscriptions', title: '5-day promo and billing live', status: 'blocked', owner: 'operations', note: 'Stripe or billing provider keys are required.' },
  { id: 'tracking', area: 'Analytics', title: 'Tracking stack configured', status: 'blocked', owner: 'marketing', note: 'PostHog, GA4, and ad pixel keys must be connected.' },
  { id: 'privacy', area: 'Legal', title: 'Privacy, cookie, opt-out, deletion, export pages', status: 'needs_review', owner: 'legal', note: 'Pages exist; qualified counsel should review before paid traffic.' },
  { id: 'domain', area: 'Launch', title: 'inqsi.app domain connected', status: 'blocked', owner: 'operations', note: 'Domain must be purchased and connected.' }
];

export function getLaunchReadiness() {
  const total = INQSI_LAUNCH_CHECKLIST.length;
  const ready = INQSI_LAUNCH_CHECKLIST.filter((item) => item.status === 'ready').length;
  const blocked = INQSI_LAUNCH_CHECKLIST.filter((item) => item.status === 'blocked').length;
  return {
    total,
    ready,
    blocked,
    percentReady: Math.round((ready / total) * 100),
    items: INQSI_LAUNCH_CHECKLIST
  };
}
