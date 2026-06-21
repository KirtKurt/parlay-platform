export type InqsiSnapshotCadence = 'base_1am' | 'market_15min' | 'live_3min' | 'manual_review';

export type InqsiSnapshotRecord = {
  sport: string;
  eventId: string;
  source: string;
  cadence: InqsiSnapshotCadence;
  capturedAt: string;
  payload: Record<string, unknown>;
};

const tableName = process.env.SNAPSHOT_TABLE_NAME;

export function getSnapshotStorageStatus() {
  return {
    ready: Boolean(tableName),
    tableName: tableName || null,
    cadences: ['base_1am', 'market_15min', 'live_3min', 'manual_review'] as InqsiSnapshotCadence[],
    message: tableName
      ? 'Snapshot storage table is configured.'
      : 'Working on it. SNAPSHOT_TABLE_NAME is required before saving verified market snapshots.'
  };
}

export async function saveSnapshot(record: InqsiSnapshotRecord) {
  const status = getSnapshotStorageStatus();
  if (!status.ready) {
    return {
      status: 'working_on_it' as const,
      saved: false,
      reason: 'missing_snapshot_storage',
      record: { ...record, payload: '[not stored]' }
    };
  }

  // Production target: write to DynamoDB with keys by sport, eventId, source, and capturedAt.
  return {
    status: 'ready' as const,
    saved: false,
    reason: 'dynamodb_write_not_connected_in_frontend_scaffold',
    tableName: status.tableName
  };
}

export async function listSnapshots(eventId?: string) {
  const status = getSnapshotStorageStatus();
  return {
    status: status.ready ? 'ready' : 'working_on_it',
    eventId: eventId || null,
    snapshots: [],
    message: status.ready ? 'Storage configured. Query connector still needs DynamoDB implementation.' : status.message
  };
}
