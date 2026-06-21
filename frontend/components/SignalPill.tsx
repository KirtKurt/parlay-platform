import { Signal } from '@/lib/mockData';

const iconMap: Record<Signal, string> = {
  STEAM: '▲',
  RESISTANCE: '▬',
  TRAP: '◆',
  REVERSAL: '↺',
  COIN_FLIP: '⟳',
  CHAOS: '⬡',
  DAC: '✓',
  MARKET_ANOMALY: '⚠'
};

const validSignals = Object.keys(iconMap) as Signal[];

function normalizeSignal(value: Signal | string): Signal {
  const normalized = String(value).trim().toUpperCase().replace(/\s+/g, '_') as Signal;
  return validSignals.includes(normalized) ? normalized : 'MARKET_ANOMALY';
}

export function SignalPill({ signal }: { signal: Signal | string }) {
  const normalized = normalizeSignal(signal);
  return <span className={`signal signal-${normalized.toLowerCase()}`}>{iconMap[normalized]} {normalized.replace('_', ' ')}</span>;
}
