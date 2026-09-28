import { NextResponse } from 'next/server';
import { AUTH_PROVIDERS, enabledProviders } from '@/lib/inqsiAuth';

export const dynamic = 'force-dynamic';

export function GET() {
  const enabled = enabledProviders();
  return NextResponse.json({
    ok: true,
    configured: enabled,
    missing: AUTH_PROVIDERS.filter((id) => !enabled.includes(id)),
    callbackBase: `${process.env.NEXTAUTH_URL || process.env.NEXT_PUBLIC_SITE_URL || 'https://inqsi.app'}/api/auth/callback`,
    note: 'Providers without console credentials stay disabled. No sportsbook credentials are stored.',
  });
}
