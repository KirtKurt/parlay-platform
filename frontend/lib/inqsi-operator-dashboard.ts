export type OperatorCard = {
  title: string;
  value: string;
  status: 'ready' | 'working_on_it' | 'needs_review';
  detail: string;
  href?: string;
};

export const OPERATOR_DASHBOARD_CARDS: OperatorCard[] = [
  { title: 'Live members', value: 'API-backed', status: 'ready', detail: 'Pulled from member status records through the operator summary API.', href: '/operator/members' },
  { title: 'Creators', value: 'API-backed', status: 'ready', detail: 'Creator codes, linked members, and attribution records are supported.', href: '/operator/creators' },
  { title: 'Attribution', value: 'API-backed', status: 'ready', detail: 'Tracks referral codes, captured visits, and locked member attribution.', href: '/operator/attribution' },
  { title: 'Data feed health', value: 'Partial', status: 'working_on_it', detail: 'Odds API upgrade and live feed verification are still Monday items.', href: '/operator/data' },
  { title: 'Market records', value: 'API-backed', status: 'ready', detail: 'Games, snapshots, and status rows are counted from storage.', href: '/operator/data' },
  { title: 'Privacy requests', value: 'Prepared', status: 'needs_review', detail: 'Deletion/export intake exists; support queue connection still needs final setup.', href: '/operator/privacy' },
  { title: 'Support', value: 'Prepared', status: 'working_on_it', detail: 'Support routing and issue queue still need connection.', href: '/operator/support' },
  { title: 'Launch readiness', value: 'Checklist', status: 'needs_review', detail: 'Shows remaining legal, QA, provider, and deployment tasks.', href: '/release-checklist' }
];

export const OPERATOR_NAV = [
  { label: 'Command Center', href: '/operator' },
  { label: 'Creators', href: '/operator/creators' },
  { label: 'Members', href: '/operator/members' },
  { label: 'Attribution', href: '/operator/attribution' },
  { label: 'Data', href: '/operator/data' },
  { label: 'Privacy', href: '/operator/privacy' },
  { label: 'Support', href: '/operator/support' }
];

export function formatStatus(status: OperatorCard['status']) {
  if (status === 'ready') return 'Ready';
  if (status === 'needs_review') return 'Needs review';
  return 'Working on it';
}
