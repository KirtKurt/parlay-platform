import Link from 'next/link';
import { sports, type SportSlug } from '@/lib/sports';

export type SportVisual = {
  slug: SportSlug;
  label: string;
  equipment: string;
  equipmentLabel: string;
  accent: string;
  description: string;
};

export const sportVisuals: Record<SportSlug, SportVisual> = {
  nfl: {
    slug: 'nfl',
    label: 'NFL',
    equipment: '🏈',
    equipmentLabel: 'football',
    accent: 'blue',
    description: 'NFL market board.'
  },
  cfb: {
    slug: 'cfb',
    label: 'CFB',
    equipment: '🏈',
    equipmentLabel: 'college football',
    accent: 'teal',
    description: 'College football market board.'
  },
  nba: {
    slug: 'nba',
    label: 'NBA',
    equipment: '🏀',
    equipmentLabel: 'basketball',
    accent: 'gold',
    description: 'NBA market board.'
  },
  ncaam: {
    slug: 'ncaam',
    label: 'NCAAM',
    equipment: '🏀',
    equipmentLabel: 'college basketball',
    accent: 'purple',
    description: 'College basketball market board.'
  },
  nhl: {
    slug: 'nhl',
    label: 'NHL',
    equipment: '🏒',
    equipmentLabel: 'hockey',
    accent: 'ice',
    description: 'NHL market board.'
  },
  mlb: {
    slug: 'mlb',
    label: 'MLB',
    equipment: '⚾',
    equipmentLabel: 'baseball',
    accent: 'cream',
    description: 'MLB market board.'
  },
  tennis: {
    slug: 'tennis',
    label: 'Tennis',
    equipment: '🎾',
    equipmentLabel: 'tennis',
    accent: 'lime',
    description: 'Tennis match board.'
  },
  soccer: {
    slug: 'soccer',
    label: 'Soccer',
    equipment: '⚽',
    equipmentLabel: 'soccer',
    accent: 'green',
    description: 'Soccer match board.'
  },
  darts: {
    slug: 'darts',
    label: 'Darts',
    equipment: '🎯',
    equipmentLabel: 'darts',
    accent: 'red',
    description: 'Darts match board.'
  },
  lacrosse: {
    slug: 'lacrosse',
    label: 'Lacrosse',
    equipment: '🥍',
    equipmentLabel: 'lacrosse',
    accent: 'aqua',
    description: 'Lacrosse market board.'
  },
  'table-tennis': {
    slug: 'table-tennis',
    label: 'Table Tennis',
    equipment: '🏓',
    equipmentLabel: 'table tennis',
    accent: 'mint',
    description: 'Table tennis match board.'
  }
};

export const teamVisuals: Record<string, { abbr: string; name: string; tone: string; number?: string; sport?: SportSlug }> = {
  'Buffalo Bills': { abbr: 'BUF', name: 'Buffalo', tone: 'blue', number: '17', sport: 'nfl' },
  'Miami Dolphins': { abbr: 'MIA', name: 'Miami', tone: 'teal', number: '10', sport: 'nfl' },
  'Dallas Cowboys': { abbr: 'DAL', name: 'Dallas', tone: 'silver', number: '4', sport: 'nfl' },
  'Philadelphia Eagles': { abbr: 'PHI', name: 'Philadelphia', tone: 'green', number: '11', sport: 'nfl' },
  Georgia: { abbr: 'UGA', name: 'Georgia', tone: 'red', number: '1', sport: 'cfb' },
  Alabama: { abbr: 'ALA', name: 'Alabama', tone: 'crimson', number: '15', sport: 'cfb' },
  'Boston Celtics': { abbr: 'BOS', name: 'Boston', tone: 'green', number: '0', sport: 'nba' },
  'Los Angeles Lakers': { abbr: 'LAL', name: 'Los Angeles', tone: 'gold', number: '23', sport: 'nba' },
  'Coastal Tech': { abbr: 'CT', name: 'Coastal Tech', tone: 'teal', number: '21', sport: 'ncaam' },
  'Example State': { abbr: 'EXS', name: 'Example State', tone: 'silver', number: '8', sport: 'ncaam' },
  Price: { abbr: 'PRI', name: 'Price', tone: 'red', number: '180', sport: 'darts' },
  Smith: { abbr: 'SMI', name: 'Smith', tone: 'blue', number: '60', sport: 'darts' },
  'Maryland': { abbr: 'MD', name: 'Maryland', tone: 'red', number: '22', sport: 'lacrosse' },
  Duke: { abbr: 'DUK', name: 'Duke', tone: 'blue', number: '2', sport: 'lacrosse' },
  Chen: { abbr: 'CHN', name: 'Chen', tone: 'mint', number: '24', sport: 'table-tennis' },
  Novak: { abbr: 'NOV', name: 'Novak', tone: 'silver', number: '7', sport: 'table-tennis' }
};

export function getTeamVisual(name: string) {
  if (teamVisuals[name]) return teamVisuals[name];
  const words = name.split(/\s+/).filter(Boolean);
  const abbr = words.length > 1 ? words.map((word) => word[0]).join('').slice(0, 3).toUpperCase() : name.slice(0, 3).toUpperCase();
  return { abbr, name, tone: 'blue', number: '00' };
}

export function SportEquipmentIcon({ slug, size = 'normal', showLabel = false }: { slug: SportSlug | string; size?: 'small' | 'normal' | 'large'; showLabel?: boolean }) {
  const visual = sportVisuals[slug as SportSlug] ?? sportVisuals.nfl;
  return (
    <span className={`sport-equipment sport-equipment-${size} accent-${visual.accent}`} aria-label={`${visual.label} icon`} title={visual.label}>
      <span className="sport-equipment-symbol">{visual.equipment}</span>
      {showLabel && <strong>{visual.label}</strong>}
    </span>
  );
}

export function SportIconStrip({ compact = false }: { compact?: boolean }) {
  return (
    <section className={`equipment-strip ${compact ? 'compact' : ''}`} aria-label="Sports available">
      {sports.map((sport) => {
        const visual = sportVisuals[sport.slug];
        return (
          <Link href={`/sports/${sport.slug}`} className={`equipment-card accent-${visual.accent}`} key={sport.slug} style={{ textDecoration: 'none' }} aria-label={`Open ${sport.label} board`}>
            <SportEquipmentIcon slug={sport.slug} />
            <strong>{sport.label}</strong>
          </Link>
        );
      })}
    </section>
  );
}

export function TeamJerseyBadge({ teamName, abbr, tone, number, size = 'normal' }: { teamName?: string; abbr?: string; tone?: string; number?: string; size?: 'small' | 'normal' | 'large' }) {
  const team = teamName ? getTeamVisual(teamName) : { abbr: abbr ?? 'SS', name: abbr ?? 'Team', tone: tone ?? 'blue', number: number ?? '00' };
  return (
    <span className={`jersey-badge jersey-${size} tone-${tone ?? team.tone}`} aria-label={`${team.abbr} team marker`} title={`${team.abbr} team marker`}>
      <span className="jersey-collar" />
      <b>{abbr ?? team.abbr}</b>
      <small>{number ?? team.number ?? '00'}</small>
    </span>
  );
}

export function TeamBadgeRow({ leftTeam, rightTeam, league }: { leftTeam: string; rightTeam: string; league?: string }) {
  const sportSlug = league ? league.toLowerCase().replace(/\s+/g, '-') : undefined;
  return (
    <div className="team-badge-row">
      {league && <SportEquipmentIcon slug={sportSlug ?? 'nfl'} size="small" />}
      <div>
        <TeamJerseyBadge teamName={leftTeam} />
        <span>{getTeamVisual(leftTeam).name}</span>
      </div>
      <b>vs</b>
      <div>
        <TeamJerseyBadge teamName={rightTeam} />
        <span>{getTeamVisual(rightTeam).name}</span>
      </div>
    </div>
  );
}

export function SportHeroPanel({ sportSlug, title, copy }: { sportSlug: SportSlug | string; title: string; copy: string }) {
  const visual = sportVisuals[sportSlug as SportSlug] ?? sportVisuals.nfl;
  return (
    <aside className={`sport-hero-panel accent-${visual.accent}`} aria-label={`${visual.label} panel`}>
      <SportEquipmentIcon slug={sportSlug} size="large" showLabel />
      <h3>{title}</h3>
      <p>{copy}</p>
      <div className="mini-equipment-line">
        <span>Market board</span>
        <span>Line movement</span>
        <span>Signal check</span>
      </div>
    </aside>
  );
}
