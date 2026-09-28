'use client';

import { useEffect, useState } from 'react';
import { formatKickoff, formatLocalClock, resolveTimeZone } from '@/lib/kickoff';

type KickoffLabelProps = {
  value?: string | null;
  serverTimeZone?: string | null;
  className?: string;
  mode?: 'kickoff' | 'clock';
};

export function KickoffLabel({
  value,
  serverTimeZone,
  className,
  mode = 'kickoff',
}: KickoffLabelProps) {
  const [zone, setZone] = useState(() => resolveTimeZone(serverTimeZone));

  useEffect(() => {
    try {
      setZone(resolveTimeZone(Intl.DateTimeFormat().resolvedOptions().timeZone));
    } catch {
      setZone(resolveTimeZone(serverTimeZone));
    }
  }, [serverTimeZone]);

  const label = mode === 'clock'
    ? formatLocalClock(value || new Date(), zone)
    : formatKickoff(value, zone);

  return <span className={className || 'kickoff-label'}>{label}</span>;
}
