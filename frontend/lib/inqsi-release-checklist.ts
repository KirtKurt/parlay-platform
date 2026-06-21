export type ReleaseStatus = 'ready' | 'working_on_it' | 'needs_review';

export type ReleaseChecklistItem = {
  id: string;
  title: string;
  status: ReleaseStatus;
  detail: string;
};

export const INQSI_RELEASE_CHECKLIST: ReleaseChecklistItem[] = [
  { id: 'domain', title: 'Domain and SSL', status: 'working_on_it', detail: 'Connect final domain and certificate.' },
  { id: 'frontend', title: 'Mobile frontend', status: 'ready', detail: 'Visual system is in place.' },
  { id: 'feeds', title: 'Verified feeds', status: 'working_on_it', detail: 'Connect source keys.' },
  { id: 'records', title: 'Record storage', status: 'working_on_it', detail: 'Connect storage table.' },
  { id: 'scoring', title: 'Signal scoring', status: 'ready', detail: 'Scoring model is built.' },
  { id: 'accounts', title: 'Accounts', status: 'working_on_it', detail: 'Connect account services.' },
  { id: 'privacy', title: 'Privacy controls', status: 'needs_review', detail: 'Controls are built and need final review.' },
  { id: 'analytics', title: 'Measurement', status: 'working_on_it', detail: 'Connect measurement keys.' },
  { id: 'qa', title: 'QA pass', status: 'working_on_it', detail: 'Run device and link checks.' }
];

export function getReleaseReadiness() {
  const total = INQSI_RELEASE_CHECKLIST.length;
  const ready = INQSI_RELEASE_CHECKLIST.filter((item) => item.status === 'ready').length;
  const needsReview = INQSI_RELEASE_CHECKLIST.filter((item) => item.status === 'needs_review').length;
  return {
    total,
    ready,
    needsReview,
    workingOnIt: total - ready - needsReview,
    releaseReady: ready === total,
    checklist: INQSI_RELEASE_CHECKLIST
  };
}
