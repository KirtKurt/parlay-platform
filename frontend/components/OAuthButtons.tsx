'use client';

import {useEffect, useState} from 'react';
import {signIn} from 'next-auth/react';

const LABELS: Record<string, string> = {
  google: 'Continue with Google',
  apple: 'Continue with Apple',
  twitter: 'Continue with X',
  reddit: 'Continue with Reddit',
  discord: 'Continue with Discord',
};

export function OAuthButtons() {
  const [configured, setConfigured] = useState<string[]>([]);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    fetch('/api/auth/status', {cache: 'no-store'})
      .then((r) => r.json())
      .then((data) => {
        setConfigured(Array.isArray(data?.configured) ? data.configured : []);
        setReady(true);
      })
      .catch(() => setReady(true));
  }, []);

  const all = ['google', 'apple', 'twitter', 'reddit', 'discord'];

  return (
    <div className="inqsi-oauth-actions">
      {all.map((id) => {
        const on = configured.includes(id);
        return (
          <button
            key={id}
            type="button"
            disabled={!on}
            onClick={() => on && signIn(id, {callbackUrl: '/arbitrage-v2'})}
          >
            {LABELS[id]}
            {!on && ready ? ' · needs console credentials' : ''}
          </button>
        );
      })}
    </div>
  );
}
