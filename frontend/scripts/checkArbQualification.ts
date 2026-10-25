/** Standalone regression checks: tsc --target ES2020 --module commonjs --strict --outDir /tmp/arb-console-check frontend/lib/arbQualification.ts frontend/scripts/checkArbQualification.ts && node /tmp/arb-console-check/scripts/checkArbQualification.js */
import { heldBackReason, isVerifiedExecutable, partitionScanRows, type ArbOpportunity } from '../lib/arbQualification';

function check(value: boolean, description: string): void {
  if (!value) throw new Error(description);
}

const verified: ArbOpportunity = {
  arb: true,
  math_arb: true,
  executable: true,
  minimum_profit: 1.25,
  validation: {
    rules_compatible: true,
    outcome_coverage: 'complete',
    executable: true,
    settlement_states: { strict_arbitrage: true, minimum_net_pnl: 1.25 },
  },
};
const unreviewed: ArbOpportunity = {
  ...verified,
  validation: { ...verified.validation, rules_compatible: false, qualification_reason: 'SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE' },
};
const incomplete: ArbOpportunity = {
  ...verified,
  validation: { ...verified.validation, outcome_coverage: 'incomplete' },
};
const unproven: ArbOpportunity = {
  ...verified,
  validation: { ...verified.validation, settlement_states: { strict_arbitrage: false, minimum_net_pnl: 1.25 } },
};
const noProfit: ArbOpportunity = { ...verified, minimum_profit: 0 };
const missingStatePnl: ArbOpportunity = {
  ...verified,
  validation: { ...verified.validation, settlement_states: { strict_arbitrage: true } },
};
check(isVerifiedExecutable(verified), 'strict positive settlement proof must qualify');
for (const row of [unreviewed, incomplete, unproven, noProfit, missingStatePnl, { arb: true }, {}]) {
  check(!isVerifiedExecutable(row), 'missing or adverse proof must fail closed');
}
const partition = partitionScanRows({
  hits: [verified, unreviewed, incomplete, unproven, noProfit],
  detected_unverified: [verified],
});
check(partition.verified.length === 1, 'only one engine hit is verified');
check(partition.held.length === 5, 'all other candidates are retained as held');
check(partition.held[4] === verified, 'upstream detected_unverified must stay held');
check(heldBackReason(unreviewed) === 'SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE', 'preserve upstream hold reason');
check(heldBackReason(unproven) === 'STRICT_EXECUTABLE_SETTLEMENT_PROOF_MISSING', 'missing proof reason');
check(partitionScanRows(null).verified.length === 0, 'empty scan safe');
console.log('ARB console qualification checks passed');
