# 2stackMLB shadow cycle 2026-10-07

Champion remains KS1-LGB+dual-Poisson. This note is shadow-only. promoted=false. No official model was trained. No KS1 p_home, lock, or ledger rewrite.

## Official ledger snapshot (read-only, run 37614183957)
- official Brier 0.23504089575019738
- n 237 graded ledger rows (locked_rows 238)
- new_grades 0
- status no_new_final_grades

## Failure taxonomy (not a retrain)
- One miss is not a retrain. n=237 is above the 30-row gate; skill vs coin is about +0.015 Brier.
- Hourly slate is still all projected (0 confirmed lineups). NYY and CWS remain sits. Do not attach a shadow side as a bet.
- BBS 429 on 2026-10-06 23:34 UTC killed the daily step via live_inputs provider capture. Later scheduled runs succeeded. Isolate-skip stays on ks1-isolate-bbs-identity-20260911 (PR 713, draft). Duplicate BBS, schema, and truncation remain hard errors.

## Shadow attach
- Do not promote 2stackMLB.
- Next shadow step is walk-forward on already-graded locks only, compared to frozen KS1 p_home. No SCHEMA change. No publish path change.
