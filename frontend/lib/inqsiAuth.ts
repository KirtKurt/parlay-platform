import type { NextAuthOptions } from 'next-auth';
import AppleProvider from 'next-auth/providers/apple';
import DiscordProvider from 'next-auth/providers/discord';
import GoogleProvider from 'next-auth/providers/google';
import RedditProvider from 'next-auth/providers/reddit';
import TwitterProvider from 'next-auth/providers/twitter';
import { createHash } from 'crypto';

export const AUTH_PROVIDERS = ['google', 'apple', 'twitter', 'reddit', 'discord'] as const;
export type AuthProviderId = (typeof AUTH_PROVIDERS)[number];

function present(value?: string) {
  return Boolean(value && value.trim() && !value.includes('your-'));
}

export function enabledProviders(): AuthProviderId[] {
  const enabled: AuthProviderId[] = [];
  if (present(process.env.GOOGLE_CLIENT_ID) && present(process.env.GOOGLE_CLIENT_SECRET)) enabled.push('google');
  if (present(process.env.APPLE_ID) && present(process.env.APPLE_SECRET)) enabled.push('apple');
  if (present(process.env.TWITTER_CLIENT_ID) && present(process.env.TWITTER_CLIENT_SECRET)) enabled.push('twitter');
  if (present(process.env.REDDIT_CLIENT_ID) && present(process.env.REDDIT_CLIENT_SECRET)) enabled.push('reddit');
  if (present(process.env.DISCORD_CLIENT_ID) && present(process.env.DISCORD_CLIENT_SECRET)) enabled.push('discord');
  return enabled;
}

export function inqsiUserIdFrom(email?: string | null, provider?: string, subject?: string) {
  const normalized = String(email || '').trim().toLowerCase();
  if (normalized) return `inqsi_${createHash('sha256').update(normalized).digest('hex').slice(0, 16)}`;
  return `inqsi_${createHash('sha256').update(`${provider}:${subject || 'unknown'}`).digest('hex').slice(0, 16)}`;
}

function providers() {
  const list = [];
  if (enabledProviders().includes('google')) {
    list.push(GoogleProvider({ clientId: process.env.GOOGLE_CLIENT_ID!, clientSecret: process.env.GOOGLE_CLIENT_SECRET!, allowDangerousEmailAccountLinking: true }));
  }
  if (enabledProviders().includes('apple')) {
    list.push(AppleProvider({ clientId: process.env.APPLE_ID!, clientSecret: process.env.APPLE_SECRET!, allowDangerousEmailAccountLinking: true }));
  }
  if (enabledProviders().includes('twitter')) {
    list.push(TwitterProvider({ clientId: process.env.TWITTER_CLIENT_ID!, clientSecret: process.env.TWITTER_CLIENT_SECRET!, version: '2.0', allowDangerousEmailAccountLinking: true }));
  }
  if (enabledProviders().includes('reddit')) {
    list.push(RedditProvider({ clientId: process.env.REDDIT_CLIENT_ID!, clientSecret: process.env.REDDIT_CLIENT_SECRET!, allowDangerousEmailAccountLinking: true }));
  }
  if (enabledProviders().includes('discord')) {
    list.push(DiscordProvider({ clientId: process.env.DISCORD_CLIENT_ID!, clientSecret: process.env.DISCORD_CLIENT_SECRET!, allowDangerousEmailAccountLinking: true }));
  }
  return list;
}

export const authOptions: NextAuthOptions = {
  secret: process.env.NEXTAUTH_SECRET || process.env.AUTH_SECRET,
  session: { strategy: 'jwt', maxAge: 30 * 24 * 60 * 60 },
  pages: { signIn: '/login', error: '/login' },
  providers: providers(),
  callbacks: {
    async jwt({ token, account, user, profile }) {
      const email = String(user?.email || (profile as { email?: string } | undefined)?.email || token.email || '');
      if (account) {
        token.inqsiUserId = inqsiUserIdFrom(email, account.provider, account.providerAccountId);
        token.providers = Array.from(new Set([...(Array.isArray(token.providers) ? token.providers as string[] : []), account.provider]));
        token.email = email || token.email;
      } else if (!token.inqsiUserId) {
        token.inqsiUserId = inqsiUserIdFrom(String(token.email || ''), 'session', String(token.sub || ''));
      }
      return token;
    },
    async session({ session, token }) {
      (session as any).inqsiUserId = token.inqsiUserId;
      (session as any).providers = token.providers || [];
      return session;
    },
    async redirect({ url, baseUrl }) {
      if (url.startsWith('/')) return `${baseUrl}${url}`;
      if (url.startsWith(baseUrl)) return url;
      return `${baseUrl}/arbitrage-v2`;
    },
  },
};
