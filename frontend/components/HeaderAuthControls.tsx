'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { clearMemberSession, getMemberSession, MemberSession } from '@/lib/memberSession';

export function HeaderAuthControls() {
  const [session, setSession] = useState<MemberSession | null>(null);

  useEffect(() => {
    setSession(getMemberSession());

    function refresh() {
      setSession(getMemberSession());
    }

    window.addEventListener('storage', refresh);
    window.addEventListener('silvers-member-session-change', refresh);

    return () => {
      window.removeEventListener('storage', refresh);
      window.removeEventListener('silvers-member-session-change', refresh);
    };
  }, []);

  if (!session) {
    return <Link className="ghost-button" href="/login" style={{ textDecoration: 'none' }}>Login</Link>;
  }

  return (
    <div className="session-actions">
      <Link className="session-pill" href="/account" style={{ textDecoration: 'none' }}>
        {session.plan} member
      </Link>
      <button
        className="ghost-button compact-button"
        type="button"
        onClick={() => {
          clearMemberSession();
          window.location.href = '/';
        }}
      >
        Log out
      </button>
    </div>
  );
}
