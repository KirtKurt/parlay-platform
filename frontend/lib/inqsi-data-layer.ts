export type InqsiProviderKind = 'odds' | 'schedule' | 'live_scores' | 'context';

export type InqsiProviderStatus = {
  kind: InqsiProviderKind;
  name: string;
  envKey: string;
  configured: boolean;
  cadence: string;
  use: string;
};

export const INQSI_PROVIDERS: InqsiProviderStatus[] = [
  {
    kind: 'odds',
    name: 'Odds provider',
    envKey: 'ODDS_API_KEY',
    configured: Boolean(process.env.ODDS_API_KEY),
    cadence: '15-minute pregame snapshots; faster where live mode is supported',
    use: 'Moneyline, spread, total, book comparison, best available line, and market movement.'
  },
  {
    kind: 'schedule',
    name: 'Schedule provider',
    envKey: 'SPORTS_SCHEDULE_API_KEY',
    configured: Boolean(process.env.SPORTS_SCHEDULE_API_KEY),
    cadence: 'Daily slate load and refresh before event windows',
    use: 'Verified sport, game, team, start-time, and event identity.'
  },
  {
    kind: 'live_scores',
    name: 'Live score provider',
    envKey: 'LIVE_SCORES_API_KEY',
    configured: Boolean(process.env.LIVE_SCORES_API_KEY),
    cadence: '3-minute close-to-live checks when provider coverage exists',
    use: 'Live status, score context, final results, and grading support.'
  },
  {
    kind: 'context',
    name: 'Context provider',
    envKey: 'SPORTS_CONTEXT_API_KEY',
    configured: Boolean(process.env.SPORTS_CONTEXT_API_KEY),
    cadence: 'Event-window refresh when available',
    use: 'Injuries, starters, weather, news, and sport-specific context flags.'
  }
];

export function getProviderReadiness() {
  const configured = INQSI_PROVIDERS.filter((provider) => provider.configured).length;
  return {
    status: configured === INQSI_PROVIDERS.length ? 'ready' : configured > 0 ? 'partial' : 'working_on_it',
    configured,
    total: INQSI_PROVIDERS.length,
    providers: INQSI_PROVIDERS,
    dataPolicy: 'Verified providers only. If a feed is missing or unavailable, InQsi returns Working on it instead of placeholder data.'
  };
}

export function assertProviderConfigured(kind: InqsiProviderKind) {
  const provider = INQSI_PROVIDERS.find((item) => item.kind === kind);
  if (!provider || !provider.configured) {
    return {
      ok: false as const,
      status: 'working_on_it' as const,
      message: `${provider?.name || kind} is not configured yet.`
    };
  }
  return { ok: true as const, status: 'ready' as const, provider };
}
