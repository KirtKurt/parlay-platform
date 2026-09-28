'use client';
import { useState } from 'react';
import { eventTeams, sportLogo, teamLogo } from '@/lib/sportLogos';

function initials(label: string) {
  const parts = String(label || '').replace(/[^a-zA-Z0-9 ]/g, ' ').trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return '?';
  if (parts.length === 1) return parts[0].slice(0, 3).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function Mark({ src, alt }: { src: string; alt: string }) {
  const [failed, setFailed] = useState(!src);
  if (failed || !src) {
    return <span className="mark fallback" aria-hidden="true">{initials(alt)}</span>;
  }
  return (
    <img
      className="mark"
      src={src}
      alt=""
      width={22}
      height={22}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
    />
  );
}

export function SportMark({ sport }: { sport: string }) {
  return <Mark src={sportLogo(sport)} alt={sport || 'sport'} />;
}

export function TeamMark({ sport, name }: { sport: string; name: string }) {
  const clean = String(name || '').replace(/[+-]?\d+(\.\d+)?/g, '').trim();
  return <Mark src={teamLogo(sport, clean)} alt={clean || 'team'} />;
}

export function EventTeams({ sport, event }: { sport: string; event: string }) {
  const [away, home] = eventTeams(event);
  return (
    <span className="event">
      <b><TeamMark sport={sport} name={away} />{away}</b>
      {home ? <b><TeamMark sport={sport} name={home} />{home}</b> : null}
    </span>
  );
}
