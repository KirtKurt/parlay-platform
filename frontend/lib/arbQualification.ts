/**
 * Presentation-only ARB qualification.
 *
 * The engine's "hits" array contains mathematically executable price
 * opportunities even when sportsbook house-rule metadata is advisory.
 * Never equate membership in that array with VERIFIED EXECUTABLE.
 * Preserve every candidate; this module only determines display labels.
 */
export type ArbOpportunity = {
  arb?: boolean;
  math_arb?: boolean;
  executable?: boolean;
  minimum_profit?: number | null;
  validation?: {
    rules_compatible?: boolean;
    outcome_coverage?: string;
    executable?: boolean;
    qualification_reason?: string;
    settlement_reason?: string;
    settlement_state_reason?: string;
    settlement_advisory?: string;
    missing_books?: string[];
    settlement_states?: {
      strict_arbitrage?: boolean;
      minimum_net_pnl?: number | null;
    };
  };
};

export function isVerifiedExecutable(row: ArbOpportunity): boolean {
  const validation = row.validation;
  const states = validation?.settlement_states;
  const minProfit = row.minimum_profit;
  const stateProfit = states?.minimum_net_pnl;
  return (
    row.arb === true &&
    row.math_arb === true &&
    row.executable === true &&
    validation?.executable === true &&
    validation.rules_compatible === true &&
    validation.outcome_coverage === 'complete' &&
    !validation.qualification_reason &&
    !validation.settlement_reason &&
    !validation.settlement_state_reason &&
    !validation.settlement_advisory &&
    (!validation.missing_books || validation.missing_books.length === 0) &&
    states?.strict_arbitrage === true &&
    typeof minProfit === 'number' &&
    Number.isFinite(minProfit) &&
    minProfit > 0 &&
    typeof stateProfit === 'number' &&
    Number.isFinite(stateProfit) &&
    stateProfit > 0
  );
}

export function partitionScanRows<T extends ArbOpportunity>(
  scan: { hits?: T[]; detected_unverified?: T[] } | null,
): { verified: T[]; held: T[] } {
  const verified: T[] = [];
  const held: T[] = [];
  for (const row of scan?.hits || []) {
    (isVerifiedExecutable(row) ? verified : held).push(row);
  }
  // The engine's detected_unverified rows remain held even if a malformed
  // upstream row appears to carry some positive qualification fields.
  held.push(...(scan?.detected_unverified || []));
  return { verified, held };
}

export function heldBackReason(row: ArbOpportunity): string {
  const validation = row.validation;
  return (
    validation?.qualification_reason ||
    validation?.settlement_reason ||
    validation?.settlement_state_reason ||
    validation?.settlement_advisory ||
    (validation?.missing_books?.length ? 'SPORTSBOOK_SETTLEMENT_RULES_MISSING' : '') ||
    (validation?.rules_compatible !== true
      ? 'SPORTSBOOK_RULES_NOT_VERIFIED_COMPATIBLE'
      : 'STRICT_EXECUTABLE_SETTLEMENT_PROOF_MISSING')
  );
}
