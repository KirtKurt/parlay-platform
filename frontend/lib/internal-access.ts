import { cookies } from 'next/headers';

export const internalCookieName = 'inqsi_internal_session';

export function isInternalPortalEnabled() {
  return process.env.INQSI_INTERNAL_PORTAL_ENABLED === 'true';
}

export function getInternalPin() {
  return process.env.INQSI_INTERNAL_PIN || '';
}

export function hasInternalSession() {
  return cookies().get(internalCookieName)?.value === 'active';
}
