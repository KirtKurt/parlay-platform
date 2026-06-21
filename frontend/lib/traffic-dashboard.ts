export type TrafficAttribute = {
  label: string;
  key: string;
  description: string;
};

export const TRAFFIC_ATTRIBUTES: TrafficAttribute[] = [
  { label: 'Visitors', key: 'visitors', description: 'Unique visitors by session or device ID.' },
  { label: 'Page views', key: 'page_views', description: 'Total page views.' },
  { label: 'Landing page', key: 'landing_page', description: 'First page viewed.' },
  { label: 'Referrer', key: 'referrer', description: 'Previous site or app when available.' },
  { label: 'UTM source', key: 'utm_source', description: 'Campaign source.' },
  { label: 'UTM medium', key: 'utm_medium', description: 'Campaign medium.' },
  { label: 'UTM campaign', key: 'utm_campaign', description: 'Campaign name.' },
  { label: 'Creator code', key: 'creator_code', description: 'Creator or partner code.' },
  { label: 'Signup started', key: 'signup_started', description: 'Started account flow.' },
  { label: 'Signup completed', key: 'signup_completed', description: 'Completed account flow.' },
  { label: 'Trial started', key: 'trial_started', description: 'Started promo access.' },
  { label: 'Live member', key: 'live_member', description: 'Converted to live membership.' }
];
