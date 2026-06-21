export type InqsiRecord = {
  sport: string;
  itemId: string;
  feedName: string;
  capturedAt: string;
  category: string;
  primaryName?: string;
  secondaryName?: string;
  primaryValue?: number;
  secondaryValue?: number;
  sharedValue?: number;
};

export function buildRecordKeys(record: InqsiRecord) {
  const date = record.capturedAt.slice(0, 10);
  return {
    pk: `SPORT#${record.sport.toUpperCase()}#DATE#${date}`,
    sk: `ITEM#${record.itemId}#FEED#${record.feedName}#TIME#${record.capturedAt}#CATEGORY#${record.category}`,
    lookupPk: `ITEM#${record.itemId}`,
    lookupSk: `TIME#${record.capturedAt}#FEED#${record.feedName}`
  };
}

export function validateRecord(record: Partial<InqsiRecord>) {
  const missing = ['sport', 'itemId', 'feedName', 'capturedAt', 'category'].filter((key) => !record[key as keyof InqsiRecord]);
  return {
    valid: missing.length === 0,
    missing,
    message: missing.length ? `Missing required fields: ${missing.join(', ')}` : 'Record is structurally valid.'
  };
}

export const RECORD_STORAGE_TARGET = {
  tableEnvKey: 'INQSI_RECORD_TABLE',
  cadence: 'Scheduled captures with faster status checks where supported',
  status: process.env.INQSI_RECORD_TABLE ? 'ready' : 'working_on_it',
  noFallbackPolicy: 'Do not invent missing values. Store only verified feed values.'
};
