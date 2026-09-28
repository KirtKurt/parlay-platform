import { Signal } from '@/lib/mockData';

const iconMap: Record<string, string> = {
  STEAM: '▲',
  RESISTANCE: '▬',
  TRAP: '◆',
  REVERSAL: '↺',
  COIN_FLIP: '⟳',
  CHAOS: '⬡',
  DAC: '✓',
  MARKET_ANOMALY: '⚠',
  ACTIVE_SLATE: '●',
  MARKET_BOARD: '▣',
  SAMPLE: '◇',
  WAITING: '○',
  NOT_LIVE: '◇'
};

const validSignals = Object.keys(iconMap);

function normalizeSignal(value: Signal | string): string | null {
  const normalized = String(value).trim().toUpperCase().replace(/\s+/g, '_');
  if (!normalized) return null;
  return validSignals.includes(normalized) ? normalized : null;
}

export function SignalPill({ signal }: { signal: Signal | string }) {
  const normalized = normalizeSignal(signal);
  if (!normalized) return null;
  if (normalized === 'SAMPLE') return null;
  if (normalized === 'ACTIVE_SLATE' || normalized === 'MARKET_BOARD') {
    return <span className="signal signal-active_slate">Live board on radar</span>;
  }
  return <span className={`signal signal-${normalized.toLowerCase()}`}>{iconMap[normalized]} {normalized.replace(/_/g, ' ')}</span>;
}
