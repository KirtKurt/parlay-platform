'use client';

import { useMemo, useState } from 'react';
import { lineMovement as fallbackLineMovement } from '@/lib/mockData';

type Point = {
  time: string;
  bufMoneyline: number;
  miaMoneyline: number;
  milestone?: string;
  signal?: string;
};

type SeriesKey = 'bufMoneyline' | 'miaMoneyline';

const series: { key: SeriesKey; label: string }[] = [
  { key: 'bufMoneyline', label: 'BUF ML' },
  { key: 'miaMoneyline', label: 'MIA ML' }
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

export function LineMovementGraph({ data = fallbackLineMovement }: { data?: Point[] }) {
  const lineMovement = data.length ? data : fallbackLineMovement;
  const [activeIndex, setActiveIndex] = useState(lineMovement.length - 1);

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
          <h3>Bills vs Dolphins moneyline path</h3>
        </div>
        <div className="movement-legend">
          {series.map((item) => (
            <span className={`legend-item legend-${item.key}`} key={item.key}>{item.label}</span>
          ))}
        </div>
      </div>

      <div className="movement-chart-wrap">
        <svg className="movement-chart" viewBox="0 0 100 100" preserveAspectRatio="none" aria-label="Line movement chart with 15-minute pulls">
          <defs>
            <linearGradient id="movementGrid" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopOpacity="0.28" />
              <stop offset="100%" stopOpacity="0.04" />
            </linearGradient>
          </defs>
          {[20, 40, 60, 80].map((y) => <line className="chart-grid" x1="0" x2="100" y1={y} y2={y} key={y} />)}
          {milestones.map((point) => {
            const x = scaleX(visibleMovement.indexOf(point), visibleMovement.length);
            return <line className="milestone-line" x1={x} x2={x} y1="0" y2="100" key={point.time} />;
          })}
          <path className="line-path line-buf" d={pathFor(visibleMovement, 'bufMoneyline', min, max)} />
          <path className="line-path line-mia" d={pathFor(visibleMovement, 'miaMoneyline', min, max)} />
          {visibleMovement.map((point, index) => {
            const x = scaleX(index, visibleMovement.length);
            const isMilestone = Boolean(point.milestone);
            return (
              <g key={point.time}>
                <circle className={isMilestone ? 'point point-buf milestone-point' : 'point point-buf'} cx={x} cy={scaleY(point.bufMoneyline, min, max)} r={isMilestone ? 1.9 : 0.9} />
                <circle className={isMilestone ? 'point point-mia milestone-point' : 'point point-mia'} cx={x} cy={scaleY(point.miaMoneyline, min, max)} r={isMilestone ? 1.9 : 0.9} />
              </g>
            );
          })}
        </svg>

        <div className="milestone-labels">
          {milestones.map((point) => (
            <span style={{ left: `${scaleX(visibleMovement.indexOf(point), visibleMovement.length)}%` }} key={point.time}>
              {point.milestone}
            </span>
          ))}
        </div>
      </div>

      <div className="pull-strip">
        {lineMovement.map((point, index) => (
          <button
            type="button"
            className={index === safeIndex ? 'pull-dot pull-dot-major' : point.milestone ? 'pull-dot pull-dot-major' : 'pull-dot'}
            title={`${point.time}: BUF ${formattedOdds(point.bufMoneyline)} / MIA ${formattedOdds(point.miaMoneyline)}${point.signal ? ` · ${point.signal}` : ''}`}
            key={point.time}
            onClick={() => setActiveIndex(index)}
            aria-label={`Show line movement through ${point.time}`}
          >
            {point.milestone ? point.milestone : index === safeIndex ? 'NOW' : ''}
          </button>
        ))}
      </div>

      <div style={{ display: 'grid', gap: '10px', margin: '0 0 18px' }}>
        <div className="rank-meta" style={{ justifyContent: 'space-between' }}>
          <span>Slide time: {activePoint.time}</span>
          <span>BUF {formattedOdds(activePoint.bufMoneyline)}</span>
          <span>MIA {formattedOdds(activePoint.miaMoneyline)}</span>
          <span>{activePoint.signal ?? 'Market capture'}</span>
        </div>
        <input
          type="range"
          min="0"
          max={lineMovement.length - 1}
          value={safeIndex}
          onChange={(event) => setActiveIndex(Number(event.target.value))}
          aria-label="Slide through 15-minute odds pulls"
        />
      </div>

      <div className="movement-footer">
        <div>
          <span>Displayed Through</span>
          <strong>{activePoint.time}</strong>
        </div>
        <div>
          <span>Path</span>
          <strong>T1 + every 15 min + T2 + every 15 min + T3</strong>
        </div>
        <div>
          <span>Signal</span>
          <strong>{activePoint.signal ?? 'Steam building without major reversal'}</strong>
        </div>
      </div>
    </section>
  );
}
