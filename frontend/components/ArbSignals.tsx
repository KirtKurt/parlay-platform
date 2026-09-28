'use client';

import { RadarStrip } from '@/components/RadarStrip';
import { radarFromArb } from '@/lib/radarSignals';

export function ArbSignals({ row }: { row: any }) {
  return <RadarStrip items={radarFromArb(row)} title="Signals on radar" />;
}
