export type InqsiEventName =
  | 'page_view'
  | 'signup_opened'
  | 'provider_missing'
  | 'data_unavailable'
  | 'signal_requested'
  | 'selection_review_requested'
  | 'performance_viewed';

export type InqsiEventPayload = Record<string, string | number | boolean | null | undefined>;

const analyticsEndpoint = process.env.NEXT_PUBLIC_ANALYTICS_ENDPOINT;

export function trackInqsiEvent(name: InqsiEventName, payload: InqsiEventPayload = {}) {
  const event = {
    name,
    payload,
    product: 'InQsi',
    timestamp: new Date().toISOString()
  };

  if (!analyticsEndpoint || typeof navigator === 'undefined') {
    return { status: 'working_on_it' as const, event };
  }

  const body = JSON.stringify(event);
  if ('sendBeacon' in navigator) {
    navigator.sendBeacon(analyticsEndpoint, body);
    return { status: 'sent' as const, event };
  }

  void fetch(analyticsEndpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    keepalive: true
  });

  return { status: 'sent' as const, event };
}

export function getMonitoringReadiness() {
  return {
    analyticsEndpointReady: Boolean(process.env.NEXT_PUBLIC_ANALYTICS_ENDPOINT),
    errorTrackingReady: Boolean(process.env.NEXT_PUBLIC_ERROR_TRACKING_DSN),
    uptimeReady: Boolean(process.env.UPTIME_MONITOR_URL),
    message: 'Working on it until analytics, error tracking, and uptime environment variables are connected.'
  };
}
