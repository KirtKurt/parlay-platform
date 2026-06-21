export type AdminMember = {
  id: string;
  name: string;
  email: string;
  status: 'trial' | 'active' | 'paused' | 'cancelled';
  source: string;
  creator: string;
  joinedAt: string;
  lastActive: string;
  publicCard: boolean;
  savedSlips: number;
  weekScore: number;
  monthScore: number;
};

export type AdminSlipReview = {
  id: string;
  member: string;
  title: string;
  visibility: 'private' | 'public';
  legs: number;
  result: 'pending' | 'hit' | 'missed';
  flag: string;
};

export type AdminSeoPage = {
  path: string;
  status: 'live' | 'alias' | 'draft';
  indexable: boolean;
  lastUpdated: string;
};

export type AdminAuditEvent = {
  id: string;
  actor: string;
  action: string;
  target: string;
  createdAt: string;
};

export type AdminFeatureFlag = {
  key: string;
  label: string;
  enabled: boolean;
  note: string;
};

export type AdminTrafficSource = {
  source: string;
  visits: number;
  trials: number;
  paid: number;
  creator: string;
};

export type AdminSupportItem = {
  id: string;
  type: string;
  status: 'open' | 'watching' | 'closed';
  member: string;
  note: string;
};

export const adminMembers: AdminMember[] = [
  {
    id: 'mem_001',
    name: 'InQsi Member',
    email: 'member@example.com',
    status: 'trial',
    source: 'organic',
    creator: 'direct',
    joinedAt: '2026-06-21',
    lastActive: 'today',
    publicCard: true,
    savedSlips: 2,
    weekScore: 67,
    monthScore: 63
  },
  {
    id: 'mem_002',
    name: 'Buffalo Market Read',
    email: 'buffalo@example.com',
    status: 'active',
    source: 'creator',
    creator: 'creator-alpha',
    joinedAt: '2026-06-18',
    lastActive: 'today',
    publicCard: true,
    savedSlips: 4,
    weekScore: 71,
    monthScore: 58
  },
  {
    id: 'mem_003',
    name: 'Three Leg Only',
    email: 'three@example.com',
    status: 'active',
    source: 'seo',
    creator: 'direct',
    joinedAt: '2026-06-17',
    lastActive: 'yesterday',
    publicCard: false,
    savedSlips: 7,
    weekScore: 62,
    monthScore: 66
  }
];

export const adminSlipReviews: AdminSlipReview[] = [
  { id: 'slip_001', member: 'InQsi Member', title: 'Saturday review card', visibility: 'public', legs: 3, result: 'pending', flag: 'Clean 3-leg cap' },
  { id: 'slip_002', member: 'Buffalo Market Read', title: 'Line movement review', visibility: 'private', legs: 3, result: 'missed', flag: 'Post-game review needed' },
  { id: 'slip_003', member: 'Three Leg Only', title: 'Member score card', visibility: 'public', legs: 3, result: 'hit', flag: 'Public score allowed' }
];

export const adminSeoPages: AdminSeoPage[] = [
  { path: '/', status: 'live', indexable: true, lastUpdated: '2026-06-21' },
  { path: '/ai-slip-scanner', status: 'live', indexable: true, lastUpdated: '2026-06-21' },
  { path: '/3-leg-parlay-guide', status: 'live', indexable: true, lastUpdated: '2026-06-21' },
  { path: '/line-movement-guide', status: 'live', indexable: true, lastUpdated: '2026-06-21' },
  { path: '/compare/inqsi-vs-pick-sellers', status: 'live', indexable: true, lastUpdated: '2026-06-21' },
  { path: '/u/inqsi-member', status: 'live', indexable: true, lastUpdated: '2026-06-21' }
];

export const adminTrafficSources: AdminTrafficSource[] = [
  { source: 'organic search', visits: 128, trials: 9, paid: 1, creator: 'direct' },
  { source: 'creator link', visits: 84, trials: 11, paid: 2, creator: 'creator-alpha' },
  { source: 'direct', visits: 52, trials: 3, paid: 1, creator: 'direct' }
];

export const adminSupportItems: AdminSupportItem[] = [
  { id: 'support_001', type: 'visibility', status: 'open', member: 'InQsi Member', note: 'Review public score-card visibility question.' },
  { id: 'support_002', type: 'access', status: 'watching', member: 'Buffalo Market Read', note: 'Monitor member access and trial conversion.' }
];

export const adminAuditEvents: AdminAuditEvent[] = [
  { id: 'audit_001', actor: 'owner', action: 'viewed admin dashboard', target: 'admin', createdAt: 'today' },
  { id: 'audit_002', actor: 'system', action: 'prepared IndexNow route', target: '/api/indexnow', createdAt: 'today' },
  { id: 'audit_003', actor: 'system', action: 'updated sitemap', target: '/sitemap.xml', createdAt: 'today' }
];

export const adminFeatureFlags: AdminFeatureFlag[] = [
  { key: 'public_cards', label: 'Public member score cards', enabled: true, note: 'Member-controlled score visibility.' },
  { key: 'comments', label: 'Public comments', enabled: false, note: 'Keep off at launch.' },
  { key: 'direct_messages', label: 'Direct messages', enabled: false, note: 'Keep off at launch.' },
  { key: 'challenges', label: 'Challenges', enabled: false, note: 'Data model ready, public route hidden.' },
  { key: 'admin_portal', label: 'Admin portal', enabled: true, note: 'Protected internal owner portal.' }
];
