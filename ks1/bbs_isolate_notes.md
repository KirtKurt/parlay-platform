# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-11-23 16:34 UTC operator cycle:
- Latest scheduled run 45215470853 (#2739) completed success (started 16:20:54Z, ~6min). Previous scheduled 45204191495 success. Artifacts: ks1-daily-45215470853, ks1-nightly-45215470853, ks1-loss-patterns. No CRON_GAP. Recent schedule runs all success. No recent failures.
- No current failure requiring isolate-skip. Isolate-skip already committed on this branch. bbs_degrade present. PR 713 draft open. Do not merge without approval.
- PR 710 open non-draft (serving gates, SCHEMA extensions). PR 711 draft (2stackMLB shadow-only). PR 712 closed (watchdog; do not activate).
- ACCURACY available: official Brier 0.2375, n=250, new_grades=0 from ks1-nightly-45215470853. Status no_new_final_grades. locked_rows=251.
- Slate: predictions.parquet 0 rows. Empty slate (offseason). NYY/NYM and CWS remain sits if present.
- Did not patch main. Did not merge. Did not rewrite p_home, locks, or ledgers. Did not dispatch workflow. Did not promote 2stackMLB. Did not start 24h watchdog.
- Champion remains KS1-LGB+dual-Poisson. 2stackMLB shadow-only.
- Next: safe 2stackMLB shadow research (walk-forward/failure taxonomy) only; no official train.

Do not merge without Kurt approval.
