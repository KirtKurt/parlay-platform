import { NextResponse } from 'next/server';
import { getAuthReadiness, getSubscriptionReadiness } from '@/lib/inqsi-auth-billing';
import { getMonitoringReadiness } from '@/lib/inqsi-observability';

export const dynamic = 'force-dynamic';

export async function GET() {
  return NextResponse.json({
    product: 'InQsi',
    status: 'working_on_it',
    auth: getAuthReadiness(),
    subscription: getSubscriptionReadiness(),
    monitoring: getMonitoringReadiness(),
    dataPolicy: 'Verified data only. Missing feeds return Working on it.'
  });
}
