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
const contradictoryReason: ArbOpportunity = {
  ...verified,
  validation: { ...verified.validation, qualification_reason: 'HOUSE_RULE_MISMATCH' },
};
const missingMathProof: ArbOpportunity = { ...verified, math_arb: false };
const adverseSettlementReason: ArbOpportunity = {
  ...verified,
  validation: { ...verified.validation, settlement_reason: 'EVENT_OR_MARKET_SCOPE_UNREVIEWED' },
};
const adverseStateReason: ArbOpportunity = {
  ...verified,
  validation: { ...verified.validation, settlement_state_reason: 'SETTLEMENT_STATE_PROOF_FAILED' },
};
const advisoryOnly: ArbOpportunity = {
  ...verified,
  validation: { ...verified.validation, settlement_advisory: 'SPORTSBOOK_RULES_MAY_AFFECT_SETTLEMENT' },
};
const missingBookRules: ArbOpportunity = {
  ...verified,
  validation: { ...verified.validation, missing_books: ['book-a'] },
};
check(isVerifiedExecutable(verified), 'strict positive settlement proof must qualify');
for (const row of [unreviewed, incomplete, unproven, noProfit, missingStatePnl, contradictoryReason, missingMathProof, adverseSettlementReason, adverseStateReason, advisoryOnly, missingBookRules, { arb: true }, {}]) {
  check(!isVerifiedExecutable(row), 'missing or adverse proof must fail closed');
}
const partition = partitionScanRows({
  hits: [verified, unreviewed, incomplete, unproven, noProfit, contradictoryReason, missingMathProof, adverseSettlementReason, adverseStateReason, advisoryOnly, missingBookRules],
  detected_unverified: [verified],
});
check(partition.verified.length === 1, 'only one engine hit is verified');
check(partition.held.length === 11, 'all other candidates are retained as held');
check(partition.held[10] === verified, 'upstream detected_unverified must stay held');
check(heldBackReason(unreviewed) === 'SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE', 'preserve upstream hold reason');
check(heldBackReason(contradictoryReason) === 'HOUSE_RULE_MISMATCH', 'explicit adverse qualification must be visible');
check(heldBackReason(unproven) === 'STRICT_EXECUTABLE_SETTLEMENT_PROOF_MISSING', 'missing proof reason');
check(heldBackReason(adverseSettlementReason) === 'EVENT_OR_MARKET_SCOPE_UNREVIEWED', 'settlement reason must be shown');
check(heldBackReason(adverseStateReason) === 'SETTLEMENT_STATE_PROOF_FAILED', 'state reason must be shown');
check(heldBackReason(advisoryOnly) === 'SPORTSBOOK_RULES_MAY_AFFECT_SETTLEMENT', 'advisory must be shown');
check(heldBackReason(missingBookRules) === 'SPORTSBOOK_SETTLEMENT_RULES_MISSING', 'missing book rules must be shown');
check(partitionScanRows(null).verified.length === 0, 'empty scan safe');
console.log('ARB console qualification checks passed');
