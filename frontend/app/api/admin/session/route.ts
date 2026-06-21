import { cookies } from 'next/headers';
import { NextRequest, NextResponse } from 'next/server';
import { getInternalPin, internalCookieName, isInternalPortalEnabled } from '@/lib/internal-access';

export async function POST(request: NextRequest) {
  if (!isInternalPortalEnabled()) {
    return NextResponse.json({ ok: false, message: 'Internal portal is disabled.' }, { status: 404 });
  }

  const formData = await request.formData();
  const pin = String(formData.get('pin') || '');

  if (!getInternalPin() || pin !== getInternalPin()) {
    return NextResponse.json({ ok: false, message: 'Invalid internal access.' }, { status: 401 });
  }

  cookies().set(internalCookieName, 'active', {
    httpOnly: true,
    sameSite: 'lax',
    secure: process.env.NODE_ENV === 'production',
    path: '/',
    maxAge: 60 * 60 * 8
  });

  return NextResponse.redirect(new URL('/admin', request.url), { status: 303 });
}

export async function DELETE() {
  cookies().delete(internalCookieName);
  return NextResponse.json({ ok: true });
}
