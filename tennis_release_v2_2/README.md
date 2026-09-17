# Tennis V2.2 — public market coverage repair

This release fixes the zero-pick failure without altering the MLB production stack.

## Root cause

The legacy odds account exposed no events for the current ATP 250/WTA 250 slate. ESPN supplied complete match rosters but no betting prices. The deployed Tennis pipeline therefore had no legitimate input from which to calculate or lock picks.

## Repair

Tennis V2.2 uses public Polymarket two-player Tennis moneyline markets as the primary market source and ESPN Tennis core data for schedule/result enrichment. No odds API credential is required.

The adapter emits the same normalized `books -> moneyline -> home/away American price` contract consumed by the source-locked MLB-derived Tennis engine. Downstream calculations and lifecycle remain unchanged:

1. discover every open two-player Tennis moneyline market;
2. retain every valid match within the future horizon;
3. calculate the earliest-match-minus-10-hours collection boundary;
4. store canonical 15-minute snapshots;
5. run American-odds conversion, de-vig, consensus, temporal movement, reversals, edge, EV, score, guardrails, confidence, and ranking;
6. write pre-lock predictions;
7. create readiness checkpoints at T−60 and T−50;
8. promote the last persisted pre-cutoff prediction at T−45 without rescoring;
9. assess playability at T−30 and T−15;
10. settle by exact Polymarket market ID, with ESPN normal-final crosswalk only when exact resolution is unavailable;
11. train and promote Tennis-only challengers.

## Isolation

The workflow updates only `parlay-platform-tennis-ml-prod`. It fingerprints `parlay-platform-dev` before and after deployment and fails if the MLB template or resource inventory changes. Tennis has separate tables, archive bucket, Lambdas, API, schedules, locks, outcomes, labels, model registry, and model artifacts.

## Truth boundary

This release is forward-only. It does not fabricate or backfill July 23 picks. Predictions begin only after a real market is inside its T−10 collection window.
