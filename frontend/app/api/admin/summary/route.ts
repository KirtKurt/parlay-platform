import { NextResponse } from 'next/server';
import { getProviderOverallStatus } from '@/lib/inqsi-data-provider';
import { getAuthReadiness, getSubscriptionReadiness } from '@/lib/inqsi-auth-billing';
import { getMonitoringReadiness } from '@/lib/inqsi-observability';
import { getLaunchReadiness } from '@/lib/inqsi-launch-checklist';

export const dynamic = 'force-dynamic';

export async function GET() {
  return NextResponse.json({
    product: 'InQsi',
    generatedAt: new Date().toISOString(),
    providerStatus: getProviderOverallStatus(),
    auth: getAuthReadiness(),
    subscription: getSubscriptionReadiness(),
    monitoring: getMonitoringReadiness(),
    launch: getLaunchReadiness(),
    note: 'Admin authentication must be connected before exposing private operational data.'
  });
}
