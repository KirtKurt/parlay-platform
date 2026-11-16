# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-11-16 16:28 UTC operator cycle:
- Latest scheduled run 43906630396 in_progress (started 16:21:45Z). Previous 43896671417 completed success (started 15:28:13Z, ~13min). Artifacts: ks1-daily-43896671417, ks1-nightly-43896671417, ks1-loss-patterns. No CRON_GAP. Recent schedule runs all success. No recent failures.
- No current failure requiring isolate-skip. Isolate-skip already committed on this branch (daily.py isolate_unmatched=True; missing_bbs_identity exclusions). bbs_degrade present. No open PR for this branch. Do not merge without approval.
- PR 710 open non-draft (serving gates; SCHEMA adds p_lgb/pick_status/selection_reason). PR 711 draft (2stackMLB shadow-only). PR 712 closed/merged (watchdog; do not activate).
- ACCURACY available: official Brier 0.2375, n=250, new_grades=0 from ks1-nightly-43896671417. Status no_new_final_grades.
- Slate: predictions.parquet / csv 0 rows, official_games=0. Empty slate (offseason). NYY/NYM and CWS remain sits if present.
- Did not patch main. Did not merge. Did not rewrite p_home, locks, or ledgers. Did not dispatch workflow. Did not promote 2stackMLB. Did not start 24h watchdog.
- Champion remains KS1-LGB+dual-Poisson. 2stackMLB shadow-only.
- Next: safe 2stackMLB shadow research (walk-forward/failure taxonomy) only; no official train.

Do not merge without Kurt approval.
