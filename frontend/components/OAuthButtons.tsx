'use client';

import {signIn} from 'next-auth/react';

const PROVIDERS = [
  {id: 'google', label: 'Continue with Google', enabled: Boolean(process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID)},
  {id: 'apple', label: 'Continue with Apple', enabled: Boolean(process.env.NEXT_PUBLIC_APPLE_CLIENT_ID)},
  {id: 'twitter', label: 'Continue with X', enabled: Boolean(process.env.NEXT_PUBLIC_TWITTER_CLIENT_ID)},
  {id: 'reddit', label: 'Continue with Reddit', enabled: Boolean(process.env.NEXT_PUBLIC_REDDIT_CLIENT_ID)},
  {id: 'discord', label: 'Continue with Discord', enabled: Boolean(process.env.NEXT_PUBLIC_DISCORD_CLIENT_ID)},
] as const;

export function OAuthButtons() {
  return (
    <div className="inqsi-oauth-actions">
      {PROVIDERS.map((provider) => (
        <button
          key={provider.id}
          type="button"
          disabled={!provider.enabled}
          onClick={() => {
            if (!provider.enabled) return;
            signIn(provider.id, {callbackUrl: '/arbitrage-v2'});
          }}
        >
          {provider.label}
          {!provider.enabled ? ' — needs console credentials' : ''}
        </button>
      ))}
    </div>
  );
}
