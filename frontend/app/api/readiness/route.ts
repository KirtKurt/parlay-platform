import { NextResponse } from 'next/server';
import { getAuthReadiness, getMembershipReadiness } from '@/lib/inqsi-auth-membership';
import { getMonitoringReadiness } from '@/lib/inqsi-observability';

export const dynamic = 'force-dynamic';

export async function GET() {
  return NextResponse.json({
    product: 'InQsi',
    status: 'working_on_it',
    auth: getAuthReadiness(),
    membership: getMembershipReadiness(),
    monitoring: getMonitoringReadiness(),
    dataPolicy: 'Verified data only. Missing feeds return Working on it.'
  });
}
