export type InqsiProviderStatus = 'ready' | 'working_on_it' | 'missing_key' | 'not_configured';

export type InqsiProviderReadiness = {
  name: string;
  purpose: string;
  status: InqsiProviderStatus;
  requiredEnv: string[];
  message: string;
};

export function getDataProviderReadiness(): InqsiProviderReadiness[] {
  const oddsReady = Boolean(process.env.ODDS_API_KEY || process.env.NEXT_PUBLIC_ODDS_API_READY === 'true');
  const scoresReady = Boolean(process.env.SCORES_API_KEY || process.env.NEXT_PUBLIC_SCORES_API_READY === 'true');
  const storageReady = Boolean(process.env.SNAPSHOT_TABLE_NAME || process.env.NEXT_PUBLIC_SNAPSHOT_STORAGE_READY === 'true');

  return [
    {
      name: 'Odds provider',
      purpose: 'Moneyline, spread, total, book comparison, and market movement snapshots.',
      status: oddsReady ? 'ready' : 'missing_key',
      requiredEnv: ['ODDS_API_KEY'],
      message: oddsReady ? 'Odds provider key detected.' : 'Working on it. Odds provider key is not connected yet.'
    },
    {
      name: 'Score provider',
      purpose: 'Schedules, event status, close-to-live status, and final outcomes.',
      status: scoresReady ? 'ready' : 'missing_key',
      requiredEnv: ['SCORES_API_KEY'],
      message: scoresReady ? 'Score provider key detected.' : 'Working on it. Score provider key is not connected yet.'
    },
    {
      name: 'Snapshot storage',
      purpose: '15-minute market snapshots, 3-minute live status snapshots, and CLV history.',
      status: storageReady ? 'ready' : 'missing_key',
      requiredEnv: ['SNAPSHOT_TABLE_NAME'],
      message: storageReady ? 'Snapshot storage detected.' : 'Working on it. Snapshot storage is not connected yet.'
    }
  ];
}

export function getProviderOverallStatus() {
  const providers = getDataProviderReadiness();
  const ready = providers.every((provider) => provider.status === 'ready');
  return {
    ready,
    providers,
    message: ready ? 'All core providers are ready.' : 'Working on it. One or more core providers still need keys or storage.'
  };
}
