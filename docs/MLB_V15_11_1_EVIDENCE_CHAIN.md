# MLB V15.11.1 Immutable Evidence Chain

The historical optimizer cannot promote a candidate from summary metrics alone. Promotion requires a complete checksum chain covering every stage that could otherwise be replaced, replayed, or edited after model selection.

## Bound stages

The chain records and validates:

1. the official schedule file SHA-256;
2. the zero-call historical request-plan SHA-256;
3. the completed paid-request ledger SHA-256;
4. the SHA-256 of every gzip snapshot envelope;
5. the aggregate snapshot-manifest SHA-256;
6. the official settled-outcomes file SHA-256;
7. the normalized T-minus-45 dataset SHA-256;
8. the dataset-manifest SHA-256;
9. the selected candidate-artifact SHA-256;
10. the conditional write-once untouched-audit claim SHA-256;
11. the final gate-report SHA-256 used by the production cutover.

A missing or changed link blocks promotion.

## Provider response validation

Historical response headers are normalized case-insensitively before request-credit accounting. The completed ledger must contain exactly the request timestamps in the immutable plan. Every referenced snapshot file must exist and match its ledger SHA-256. The envelope request timestamp must match the planned timestamp, and the provider timestamp may not occur after the requested point in time.

## Untouched audit claim

The audit command requires a unique `s3://bucket/key` claim object. It writes that object with `If-None-Match: *` before opening the audit partition. A second attempt receives an S3 conditional-write failure and stops.

The claim binds:

- experiment ID;
- candidate ID;
- candidate-artifact SHA-256;
- dataset SHA-256;
- dataset-manifest SHA-256.

If audit execution fails after the claim is written, the claim remains consumed. This is intentionally fail-safe: the same audit cannot be reopened and used for iterative tuning.

## Production behavior

Passing the evidence chain does not itself deploy anything. The statistical daily-slate gate must also pass, the exact production confirmation phrase must be supplied, and execution must be explicitly enabled.

The cutover transaction then installs the approved checksum-bound champion and the write-once `HISTORICAL_DAILY_OPTIMIZER_ONLY` authority record together. Legacy selection, fallback, and automatic restore are disabled after that transaction. A missing or mismatched champion fails closed. Automatic wagering remains disabled.
