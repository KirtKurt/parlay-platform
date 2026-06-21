export type MemberRole = 'REGISTERED' | 'SUBSCRIBER' | 'MASTER';
export type MemberPlan = 'Member' | 'Full Access' | 'Master';

export type MemberSession = {
  email: string;
  name?: string;
  role: MemberRole;
  plan: MemberPlan;
  startedAt: string;
  promoEndsAt?: string;
};

export const memberSessionKey = 'inqsi_member_session_v1';
const memberSessionEvent = 'inqsi-member-session-change';

function isBrowser() {
  return typeof window !== 'undefined';
}

export function getMemberSession(): MemberSession | null {
  if (!isBrowser()) return null;

  try {
    const raw = window.localStorage.getItem(memberSessionKey);
    if (!raw) return null;
    return JSON.parse(raw) as MemberSession;
  } catch {
    return null;
  }
}

export function saveMemberSession(session: MemberSession) {
  if (!isBrowser()) return;
  window.localStorage.setItem(memberSessionKey, JSON.stringify(session));
  window.dispatchEvent(new Event(memberSessionEvent));
}

export function clearMemberSession() {
  if (!isBrowser()) return;
  window.localStorage.removeItem(memberSessionKey);
  window.dispatchEvent(new Event(memberSessionEvent));
}

export function createDemoMemberSession(email: string, plan: MemberPlan = 'Full Access'): MemberSession {
  const startedAt = new Date();
  const promoEndsAt = new Date(startedAt);
  promoEndsAt.setDate(promoEndsAt.getDate() + 5);

  return {
    email,
    role: plan === 'Master' ? 'MASTER' : 'SUBSCRIBER',
    plan,
    startedAt: startedAt.toISOString(),
    promoEndsAt: promoEndsAt.toISOString()
  };
}
