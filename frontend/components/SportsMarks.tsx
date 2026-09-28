'use client';

import {useState} from 'react';
import {eventTeams, sportMark, teamLogo} from '@/lib/sportsLogos';

function initials(label: string) {
  const bits = String(label || '').replace(/[^a-zA-Z0-9 ]/g, ' ').trim().split(/\s+/).filter(Boolean);
  if (!bits.length) return '?';
  if (bits.length === 1) return bits[0].slice(0, 3).toUpperCase();
  return (bits[0][0] + bits[bits.length - 1][0]).toUpperCase();
}

function Mark({src, alt}:{src?: string | null; alt: string}) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) {
    return <span className="team-mark fallback" aria-hidden="true">{initials(alt)}</span>;
  }
  return (
    <img
      className="team-mark"
      src={src}
      alt=""
      width={20}
      height={20}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
    />
  );
}

export function SportMark({sport}:{sport:string}) {
  const mark = sportMark(sport);
  return <Mark src={mark?.src} alt={sport || 'Sport'} />;
}

export function EventMarks({sport, event}:{sport:string; event:string}) {
  const [home, away] = eventTeams(event);
  return (
    <span className="event-marks">
      <Mark src={teamLogo(sport, home)} alt={home} />
      <b>{home}</b>
      {away ? <><Mark src={teamLogo(sport, away)} alt={away} /><b>{away}</b></> : null}
    </span>
  );
}
