# Tennis V2.1 MLB-process-flow parity deployment

This branch is an isolated operational deployment vehicle for the Tennis system. It does not modify the MLB SAM template, MLB runtime package, MLB tables, MLB schedules, MLB locks, MLB model registry, or MLB data.

## Source baseline

The release is pinned to the current production blobs for:

- `hello_world/mlb_game_winner_engine.py`: `37667544e1a44c47be11468235d4f6f799de463e`
- `hello_world/mlb_temporal_features_v1.py`: `4b372c72fb047979f592b017bcb08f65dff435c5`
- `hello_world/mlb_daily_per_game_lock_patch.py`: `072a346cffd319c3646b357cd68884698d52274d`

The deployment workflow verifies those exact blobs before it may touch AWS.

## Tennis translation

- all active Tennis competition keys and every available match;
- earliest match minus ten hours, floored to the prior 15-minute heartbeat;
- canonical 15-minute pull history;
- copied American-odds, de-vig, consensus, temporal movement, reversal, edge, EV, score, guardrail, confidence, and ranking calculations;
- one immutable no-rescore T-minus-45 lock per match;
- T-minus-60/T-minus-50 readiness and T-minus-30/T-minus-15 playability checkpoints;
- exact-event settlement with a bounded ATP/WTA normal-final fallback;
- Tennis-only labels, chronological challenger training, registry, and guarded champion promotion.

## Release identity

Archive SHA-256:

```text
1fa9306e2b675914e58b25bf766d412bada046bc81534c7f71655a979520a8bd
```

The workflow first updates `parlay-platform-tennis-ml-prod` with all schedules disabled. It validates the read APIs, performs a signed live all-key discovery and forced complete pipeline pull, confirms persisted predictions, then enables ingestion, per-match locking, settlement, and training. It fingerprints the production MLB CloudFormation stack before and after and fails if MLB changes.
