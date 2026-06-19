import type { Ranking } from '@/lib/mockData';

export function RankingCard({ ranking }: { ranking: Ranking }) {
  return (
    <article className={`rank-card ${ranking.topZone ? 'top-zone' : ''}`}>
      <div className="rank-head">
        <span>Rank #{ranking.rank}</span>
        {ranking.topZone && <b>TOP-3</b>}
      </div>
      <h4>{ranking.legs.join(' × ')}</h4>
      <div className="rank-meta">
        <span>{ranking.american}</span>
        <span>{ranking.implied}</span>
        <span>{ranking.structure}</span>
      </div>
      <p>{ranking.note}</p>
      <div className={`risk risk-${ranking.risk.toLowerCase()}`}>{ranking.risk} RISK</div>
    </article>
  );
}
