# MLB autonomous controller v1

Purpose: keep MLB research moving without conversational prompts while preserving existing production safeguards.

The controller must run on a persistent schedule and choose the next bounded research action from versioned research artifacts. It may execute challenger-only research, but it has no authority to change serving predictions, locked rows, grading, calibration thresholds, IAM, costs, or production promotion gates.

Required v1 loop:

1. Read the canonical research dataset pointer and feature-discovery report.
2. Acquire a versioned lease so only one controller cycle runs at a time.
3. If data or discovery evidence is missing, persist a WAIT task and exit successfully.
4. If repeatable feature candidates exist, choose up to five highest-ranked candidates.
5. Build deterministic multivariate challenger combinations from those candidates.
6. Evaluate challengers only on chronological development folds; preserve a separate outer holdout whose labels are not inspected by the controller.
7. Persist every cycle, candidate set, implementation hash, dataset hash, metrics, rejection, and next action in versioned research storage.
8. Never promote a challenger automatically in v1. Promotion remains behind the existing untouched-holdout and prospective evidence contract.
9. Repeat automatically on schedule. Identical dataset + implementation + candidate set must reuse prior evidence rather than rerun endlessly.
10. Fail isolated: controller failures may not break canonical dataset publication or KS1 production prediction refreshes.

Required safety invariants:

- productionAuthorityChanged=false in every controller artifact
- automaticPromotionEnabled=false
- no future-data leakage
- no random train/test split
- no mutation of immutable prediction or grading evidence
- no other-sport changes
- no direct wager placement
- no new AWS resources in v1 unless already part of the existing research storage/schedule path

Implementation should reuse `mlb_research_store_v1.Store`, `mlb_feature_discovery_runner_v1.development_slice`, `mlb_research_models_v1`, and `mlb_research_provenance_v1` rather than creating a competing research system.

Acceptance requires unit tests, the existing MLB research/provenance suite, and the MLB production-source contract to pass before merge.
