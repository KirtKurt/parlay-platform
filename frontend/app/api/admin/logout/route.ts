import { cookies } from 'next/headers';
import { NextRequest, NextResponse } from 'next/server';
import { internalCookieName } from '@/lib/internal-access';

export async function GET(request: NextRequest) {
  cookies().delete(internalCookieName);
  return NextResponse.redirect(new URL('/admin/login', request.url), { status: 303 });
}
