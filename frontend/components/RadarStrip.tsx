import { RadarItem } from '@/lib/radarSignals';

export function RadarStrip({ items, title = 'On our radar' }: { items: RadarItem[]; title?: string }) {
  return (
    <div className="radar-strip">
      <p className="eyebrow" style={{ marginBottom: 8 }}>{title}</p>
      <div className="signal-row">
        {items.map((item) => (
          <span key={item.id} className={`signal signal-${item.tone === 'held' ? 'resistance' : item.tone === 'wait' ? 'waiting' : 'active_slate'}`}>
            {item.label}
          </span>
        ))}
      </div>
    </div>
  );
}
