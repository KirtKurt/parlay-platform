import { cookies } from 'next/headers';
import { NextRequest, NextResponse } from 'next/server';

const cookieName = 'inqsi_admin_session';

function adminEnabled() {
  return process.env.INQSI_ADMIN_PORTAL_ENABLED === 'true';
}

function expectedPin() {
  return process.env.INQSI_ADMIN_PIN || '';
}

export async function POST(request: NextRequest) {
  if (!adminEnabled()) {
    return NextResponse.json({ ok: false, message: 'Admin portal is disabled.' }, { status: 404 });
  }

  const formData = await request.formData();
  const pin = String(formData.get('pin') || '');

  if (!expectedPin() || pin !== expectedPin()) {
    return NextResponse.json({ ok: false, message: 'Invalid admin access.' }, { status: 401 });
  }

  cookies().set(cookieName, 'active', {
    httpOnly: true,
    sameSite: 'lax',
    secure: process.env.NODE_ENV === 'production',
    path: '/',
    maxAge: 60 * 60 * 8
  });

  return NextResponse.redirect(new URL('/admin', request.url), { status: 303 });
}

export async function DELETE() {
  cookies().delete(cookieName);
  return NextResponse.json({ ok: true });
}

export function hasAdminSession() {
  return cookies().get(cookieName)?.value === 'active';
}
