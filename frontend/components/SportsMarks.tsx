'use client';

import {useState} from 'react';
import {eventTeams, sportMark, teamLogo} from '@/lib/sportsLogos';

function initials(label: string) {
  const bits = String(label || '').replace(/[^a-zA-Z0-9 ]/g, ' ').trim().split(/\s+/).filter(Boolean);
  if (!bits.length) return '?';
  if (bits.length === 1) return bits[0].slice(0, 3).toUpperCase();
  return (bits[0][0] + bits[bits.length - 1][0]).toUpperCase();
}

function sportKey(label: string) {
  return String(label || '').toUpperCase().replace(/[^A-Z]/g, '');
}

function SportSvg({kind}:{kind:string}) {
  const k = sportKey(kind);
  const common = {width:20, height:20, viewBox:'0 0 20 20', className:'team-mark svg-mark', 'aria-hidden':true as const};
  if (k.includes('MLB') || k.includes('BASEBALL')) {
    return <svg {...common}><circle cx="10" cy="10" r="8" fill="none" stroke="#e8edf2" strokeWidth="1.4"/><path d="M4 6c3 3 9 3 12 0M4 14c3-3 9-3 12 0" fill="none" stroke="#e8edf2" strokeWidth="1.2"/></svg>;
  }
  if (k.includes('NBA') || k.includes('WNBA') || k.includes('BASKETBALL')) {
    return <svg {...common}><circle cx="10" cy="10" r="8" fill="none" stroke="#ff6b2c" strokeWidth="1.4"/><path d="M10 2v16M2 10h16M4.2 5c3 2.2 8.6 2.2 11.6 0M4.2 15c3-2.2 8.6-2.2 11.6 0" fill="none" stroke="#ff6b2c" strokeWidth="1.1"/></svg>;
  }
  if (k.includes('NFL') || (k.includes('FOOTBALL') && !k.includes('NCAA'))) {
    return <svg {...common}><ellipse cx="10" cy="10" rx="8.2" ry="5.4" fill="none" stroke="#c9a46a" strokeWidth="1.4"/><path d="M10 4.6v10.8M7.2 10h5.6" fill="none" stroke="#c9a46a" strokeWidth="1.2"/></svg>;
  }
  if (k.includes('NHL') || k.includes('HOCKEY')) {
    return <svg {...common}><path d="M10 2.4 17.2 10 10 17.6 2.8 10Z" fill="none" stroke="#7ec8ff" strokeWidth="1.4"/><circle cx="10" cy="10" r="2" fill="#7ec8ff"/></svg>;
  }
  if (k.includes('SOCCER') || k.includes('MLS') || k.includes('EPL')) {
    return <svg {...common}><circle cx="10" cy="10" r="8" fill="none" stroke="#d7dde4" strokeWidth="1.4"/><path d="M10 4.2 12.4 8l4 .4-3 2.8.8 3.8L10 13.2 5.8 15l.8-3.8-3-2.8 4-.4Z" fill="none" stroke="#d7dde4" strokeWidth="1.1"/></svg>;
  }
  if (k.includes('TENNIS')) {
    return <svg {...common}><circle cx="10" cy="10" r="8" fill="none" stroke="#c6ff4a" strokeWidth="1.4"/><path d="M4 6c4 2 8 8 12 8M4 14c4-2 8-8 12-8" fill="none" stroke="#c6ff4a" strokeWidth="1.1"/></svg>;
  }
  if (k.includes('UFC') || k.includes('MMA')) {
    return <svg {...common}><rect x="3" y="3" width="14" height="14" rx="3" fill="none" stroke="#ff5a5a" strokeWidth="1.4"/><path d="M7 10h6M10 7v6" stroke="#ff5a5a" strokeWidth="1.3"/></svg>;
  }
  return (
    <svg {...common}>
      <path d="M4 16V5.2L10 3l6 2.2V16l-6 2Z" fill="#143044" stroke="#8fb4cc" strokeWidth="1.2"/>
    </svg>
  );
}

function TeamSvg({label}:{label:string}) {
  const text = initials(label);
  return (
    <svg className="team-mark svg-mark" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
      <path d="M3.5 16.2V4.8L10 2.6l6.5 2.2v11.4L10 18.2Z" fill="#10202c" stroke="#8fb4cc" strokeWidth="1.1"/>
      <text x="10" y="12.2" textAnchor="middle" fontSize={text.length>2?6.2:7.4} fontWeight="800" fill="#e8f1f8" fontFamily="Inter,system-ui,sans-serif">{text}</text>
    </svg>
  );
}

function Mark({src, alt, kind='team'}:{src?: string | null; alt: string; kind?: 'team' | 'sport'}) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) {
    return kind === 'sport' ? <SportSvg kind={alt} /> : <TeamSvg label={alt} />;
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
  return <Mark src={mark?.src} alt={sport || 'Sport'} kind="sport" />;
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
