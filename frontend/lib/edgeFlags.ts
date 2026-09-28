import { createClient, type EdgeConfigClient } from '@vercel/edge-config';

/**
 * Caching layers
 * 1. Vercel replica — getAll() reads the Edge Config pop copy (~1ms), not the origin store.
 * 2. Isolate TTL — warm Node/Edge isolates reuse one parse for FLAG_TTL_MS.
 * 3. Request headers — middleware reads once and forwards x-inqsi-* so route handlers skip getAll().
 * Never put secrets here. Never wrap getAll() in Next fetch cache / ISR.
 */
export const FLAG_TTL_MS = 15_000;

export type EdgeFlags = {
  arbLive: boolean;
  boardLive: boolean;
  calcEnabled: boolean;
  blockedRegions: string[];
};

export const DEFAULT_EDGE_FLAGS: EdgeFlags = {
  arbLive: true,
  boardLive: true,
  calcEnabled: true,
  blockedRegions: [],
};

const HEADER = {
  arb: 'x-inqsi-arb-live',
  board: 'x-inqsi-board-live',
  calc: 'x-inqsi-calc-enabled',
  regions: 'x-inqsi-blocked-regions',
} as const;

let client: EdgeConfigClient | null | undefined;
let isolateCache: { value: EdgeFlags; exp: number } | null = null;

function bool(value: unknown, fallback: boolean) {
  if (typeof value === 'boolean') return value;
  if (value === 'true' || value === '1') return true;
  if (value === 'false' || value === '0') return false;
  return fallback;
}

function regions(value: unknown) {
  if (Array.isArray(value)) return value.map(String).filter(Boolean);
  if (typeof value === 'string' && value.trim()) {
    return value.split(',').map((part) => part.trim()).filter(Boolean);
  }
  return [] as string[];
}

function parse(raw: Record<string, unknown> | undefined): EdgeFlags {
  const source = raw || {};
  return {
    arbLive: bool(source.arb_live, DEFAULT_EDGE_FLAGS.arbLive),
    boardLive: bool(source.board_live, DEFAULT_EDGE_FLAGS.boardLive),
    calcEnabled: bool(source.calc_enabled, DEFAULT_EDGE_FLAGS.calcEnabled),
    blockedRegions: regions(source.blocked_regions),
  };
}

function getClient() {
  if (client !== undefined) return client;
  const connection = process.env.EDGE_CONFIG?.trim();
  client = connection ? createClient(connection) : null;
  return client;
}

export async function getEdgeFlags(): Promise<EdgeFlags> {
  const now = Date.now();
  if (isolateCache && now < isolateCache.exp) return isolateCache.value;
  const store = getClient();
  if (!store) {
    isolateCache = { value: DEFAULT_EDGE_FLAGS, exp: now + FLAG_TTL_MS };
    return isolateCache.value;
  }
  try {
    const raw = await store.getAll<Record<string, unknown>>();
    const value = parse(raw);
    isolateCache = { value, exp: now + FLAG_TTL_MS };
    return value;
  } catch {
    const value = isolateCache?.value || DEFAULT_EDGE_FLAGS;
    isolateCache = { value, exp: now + Math.min(FLAG_TTL_MS, 5_000) };
    return value;
  }
}

export function flagsFromHeaders(headers: Headers): EdgeFlags | null {
  const arb = headers.get(HEADER.arb);
  if (arb === null) return null;
  return {
    arbLive: arb !== '0',
    boardLive: headers.get(HEADER.board) !== '0',
    calcEnabled: headers.get(HEADER.calc) !== '0',
    blockedRegions: regions(headers.get(HEADER.regions)),
  };
}

export async function flagsForRequest(headers: Headers): Promise<EdgeFlags> {
  return flagsFromHeaders(headers) || getEdgeFlags();
}

export function flagHeaders(flags: EdgeFlags): Record<string, string> {
  return {
    [HEADER.arb]: flags.arbLive ? '1' : '0',
    [HEADER.board]: flags.boardLive ? '1' : '0',
    [HEADER.calc]: flags.calcEnabled ? '1' : '0',
    [HEADER.regions]: flags.blockedRegions.join(','),
  };
}
