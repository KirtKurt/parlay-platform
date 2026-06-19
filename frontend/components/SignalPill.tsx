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

export function SignalPill({ signal }: { signal: Signal }) {
  return <span className={`signal signal-${signal.toLowerCase()}`}>{iconMap[signal]} {signal.replace('_', ' ')}</span>;
}
