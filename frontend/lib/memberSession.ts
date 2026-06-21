export type MemberRole = 'REGISTERED' | 'SUBSCRIBER' | 'MASTER';

export type MemberSession = {
  email: string;
  name?: string;
  role: MemberRole;
  plan: 'Core' | 'Pro' | 'Master';
  startedAt: string;
  freeWeekEndsAt?: string;
};

export const memberSessionKey = 'inqsi_member_session_v1';
const legacyMemberSessionKey = 'silvers_member_session_v1';
const memberSessionEvent = 'inqsi-member-session-change';

function isBrowser() {
  return typeof window !== 'undefined';
}

export function getMemberSession(): MemberSession | null {
  if (!isBrowser()) return null;

  try {
    const raw = window.localStorage.getItem(memberSessionKey) || window.localStorage.getItem(legacyMemberSessionKey);
    if (!raw) return null;
    return JSON.parse(raw) as MemberSession;
  } catch {
    return null;
  }
}

export function saveMemberSession(session: MemberSession) {
  if (!isBrowser()) return;
  window.localStorage.setItem(memberSessionKey, JSON.stringify(session));
  window.localStorage.removeItem(legacyMemberSessionKey);
  window.dispatchEvent(new Event(memberSessionEvent));
}

export function clearMemberSession() {
  if (!isBrowser()) return;
  window.localStorage.removeItem(memberSessionKey);
  window.localStorage.removeItem(legacyMemberSessionKey);
  window.dispatchEvent(new Event(memberSessionEvent));
}

export function createDemoMemberSession(email: string, plan: 'Core' | 'Pro' | 'Master' = 'Pro'): MemberSession {
  const startedAt = new Date();
  const freeWeekEndsAt = new Date(startedAt);
  freeWeekEndsAt.setDate(freeWeekEndsAt.getDate() + 7);

  return {
    email,
    role: plan === 'Master' ? 'MASTER' : 'SUBSCRIBER',
    plan,
    startedAt: startedAt.toISOString(),
    freeWeekEndsAt: freeWeekEndsAt.toISOString()
  };
}
