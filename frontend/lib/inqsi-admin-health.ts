import { getProviderReadiness } from '@/lib/inqsi-data-layer';
import { ACCESS_READINESS } from '@/lib/inqsi-access-control';
import { RECORD_STORAGE_TARGET } from '@/lib/inqsi-record-store';
import { getMonitoringReadiness } from '@/lib/inqsi-observability';

export function getAdminHealth() {
  const data = getProviderReadiness();
  const monitoring = getMonitoringReadiness();
  return {
    generatedAt: new Date().toISOString(),
    product: 'InQsi',
    dataLayer: data.status,
    recordStorage: RECORD_STORAGE_TARGET.status,
    access: ACCESS_READINESS.status,
    monitoring,
    actionItems: [
      data.status === 'ready' ? null : 'Connect verified feed keys.',
      RECORD_STORAGE_TARGET.status === 'ready' ? null : 'Connect record storage table.',
      ACCESS_READINESS.status === 'ready' ? null : 'Connect auth and billing keys.',
      monitoring.analyticsEndpointReady ? null : 'Connect analytics endpoint.',
      monitoring.errorTrackingReady ? null : 'Connect error tracking.',
      monitoring.uptimeReady ? null : 'Connect uptime monitoring.'
    ].filter((item): item is string => Boolean(item))
  };
}
