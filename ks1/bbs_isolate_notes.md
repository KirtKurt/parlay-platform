# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-11-09 10:20 UTC operator cycle:
- Latest scheduled run 42593180700 completed success (started 09:24:24Z, duration ~8min). Artifacts: ks1-daily-42593180700, ks1-nightly-42593180700, ks1-loss-patterns. No CRON_GAP (last ~55min ago, hourly cadence). All recent schedule runs success. No push failures recent.
- No current failure. Isolate-skip already committed on this branch (daily.py bbs_assignments isolate_unmatched=True). bbs_degrade not on main. No open PR for this branch. Do not merge without approval.
- PR 710 open non-draft (serving gates; SCHEMA adds p_lgb/pick_status/selection_reason). PR 711 draft (2stackMLB shadow-only). PR 712 closed/merged (watchdog; do not activate).
- ACCURACY available: official Brier 0.2375, n=250, new_grades=0 from ks1-nightly-42593180700. Status no_new_final_grades.
- Slate: predictions.parquet / csv 0 rows, official_games=0, confirmed_lineups=0, projected=0. Empty slate (offseason). NYY/NYM and CWS remain sits if present.
- Did not patch main. Did not merge. Did not rewrite p_home, locks, or ledgers. Did not dispatch workflow. Did not promote 2stackMLB. Did not start 24h watchdog.
- Champion remains KS1-LGB+dual-Poisson. 2stackMLB shadow-only.
- Next: safe 2stackMLB shadow research (walk-forward/failure taxonomy) only; no official train.

Do not merge without Kurt approval.
