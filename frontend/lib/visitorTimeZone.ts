import { headers } from 'next/headers';
import { resolveTimeZone } from '@/lib/kickoff';

export function visitorTimeZone() {
  try {
    const incoming = headers();
    const zone = incoming.get('x-vercel-ip-timezone') || incoming.get('x-inqsi-timezone');
    return resolveTimeZone(zone);
  } catch {
    return resolveTimeZone();
  }
}
