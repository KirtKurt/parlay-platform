import { RadarItem } from '@/lib/radarSignals';

export function RadarStrip({ items, title = 'On our radar' }: { items: RadarItem[]; title?: string }) {
  return (
    <div className="radar-strip">
      <p className="eyebrow" style={{ marginBottom: 8 }}>{title}</p>
      <div className="signal-row">
        {items.map((item) => (
          <span key={item.id} className={`signal signal-${item.tone === 'held' ? 'resistance' : item.tone === 'wait' ? 'waiting' : 'active_slate'}`}>
            {item.tone === 'held' ? 'Hold' : item.tone === 'wait' ? 'Wait' : 'On radar'} · {item.label}
          </span>
        ))}
      </div>
      <p className="movement" style={{ marginTop: 8, marginBottom: 0 }}>
        These chips show which checks are on the radar. InQsi does not publish the scoring math.
      </p>
    </div>
  );
}
