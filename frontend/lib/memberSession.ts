export type MemberRole = 'REGISTERED' | 'SUBSCRIBER' | 'MASTER';

export type MemberSession = {
  email: string;
  name?: string;
  role: MemberRole;
  plan: 'Core' | 'Pro' | 'Master';
  startedAt: string;
  freeWeekEndsAt?: string;
};

export const memberSessionKey = 'silvers_member_session_v1';

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
  window.dispatchEvent(new Event('silvers-member-session-change'));
}

export function clearMemberSession() {
  if (!isBrowser()) return;
  window.localStorage.removeItem(memberSessionKey);
  window.dispatchEvent(new Event('silvers-member-session-change'));
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
