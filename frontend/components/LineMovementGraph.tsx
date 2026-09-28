'use client';

import { useMemo, useState } from 'react';

type Point = {
  time: string;
  bufMoneyline: number;
  miaMoneyline: number;
  milestone?: string;
  signal?: string;
};

type SeriesKey = 'bufMoneyline' | 'miaMoneyline';

const series: { key: SeriesKey; label: string }[] = [
  { key: 'bufMoneyline', label: 'Away ML' },
  { key: 'miaMoneyline', label: 'Home ML' }
];

function scaleX(index: number, total: number) {
  if (total <= 1) return 0;
  return (index / (total - 1)) * 100;
}

function scaleY(value: number, min: number, max: number) {
  if (max === min) return 50;
  return 100 - ((value - min) / (max - min)) * 100;
}

function pathFor(data: Point[], key: SeriesKey, min: number, max: number) {
  return data
    .map((point, index) => {
      const x = scaleX(index, data.length);
      const y = scaleY(point[key], min, max);
      return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(' ');
}

function formattedOdds(value: number) {
  return value > 0 ? `+${value}` : `${value}`;
}

export function LineMovementGraph({ data = [] }: { data?: Point[] }) {
  const lineMovement = Array.isArray(data) ? data : [];
  const [activeIndex, setActiveIndex] = useState(Math.max(0, lineMovement.length - 1));

  if (!lineMovement.length) {
    return (
      <section className="panel movement-panel">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">Interactive 15-minute Line Movement</p>
            <h3>Waiting</h3>
          </div>
        </div>
        <p className="movement">Live pull history for this matchup is not on the board yet.</p>
      </section>
    );
  }

  const safeIndex = Math.min(activeIndex, lineMovement.length - 1);
  const visibleMovement = useMemo(() => lineMovement.slice(0, safeIndex + 1), [lineMovement, safeIndex]);
  const activePoint = lineMovement[safeIndex];
  const allValues = lineMovement.flatMap((point) => [point.bufMoneyline, point.miaMoneyline]);
  const min = Math.min(...allValues) - 5;
  const max = Math.max(...allValues) + 5;
  const milestones = visibleMovement.filter((point) => point.milestone);

  return (
    <section className="panel movement-panel">
      <div className="panel-header compact movement-header">
        <div>
          <p className="eyebrow">Interactive 15-minute Line Movement</p>
          <h3>Moneyline path from every snapshot</h3>
        </div>
        <div className="movement-legend">
          {series.map((item) => (
            <span className={`legend-item legend-${item.key}`} key={item.key}>{item.label}</span>
          ))}
        </div>
      </div>
      <div className="movement-chart-wrap">
        <svg className="movement-chart" viewBox="0 0 100 100" preserveAspectRatio="none" aria-label="Line movement chart with 15-minute pulls">
          {[20, 40, 60, 80].map((y) => <line className="chart-grid" x1="0" x2="100" y1={y} y2={y} key={y} />)}
          <path className="line-path line-buf" d={pathFor(visibleMovement, 'bufMoneyline', min, max)} />
          <path className="line-path line-mia" d={pathFor(visibleMovement, 'miaMoneyline', min, max)} />
        </svg>
      </div>
      <div className="rank-meta" style={{ justifyContent: 'space-between' }}>
        <span>Slide time: {activePoint.time}</span>
        <span>Away {formattedOdds(activePoint.bufMoneyline)}</span>
        <span>Home {formattedOdds(activePoint.miaMoneyline)}</span>
      </div>
      <input
        type="range"
        min="0"
        max={lineMovement.length - 1}
        value={safeIndex}
        onChange={(event) => setActiveIndex(Number(event.target.value))}
        aria-label="Slide through 15-minute odds pulls"
      />
    </section>
  );
}
