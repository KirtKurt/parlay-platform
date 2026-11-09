# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-11-09 09:30 UTC operator cycle:
- Latest scheduled run 42585882348 completed success (started 08:25:50Z, duration ~13.5min). Artifacts: ks1-daily-42585882348, ks1-nightly-42585882348. No CRON_GAP (last ~1h ago, hourly cadence). All recent schedule runs success.
- No current failure. Isolate-skip already committed on this branch (daily.py bbs_assignments isolate_unmatched=True, bbs_degrade for 403/429) and present on main. PR 713 draft open. Do not merge without approval.
- PR 710 open non-draft (serving gates; SCHEMA adds p_lgb/pick_status/selection_reason). PR 711 draft (2stackMLB shadow-only). PR 712 closed/merged (watchdog; do not activate).
- ACCURACY available: official Brier 0.2375, n=250, new_grades=0 from ks1-nightly-42585882348. Status no_new_final_grades.
- Slate: predictions.parquet / csv 0 rows, official_games=0, confirmed_lineups=0, projected=0. Empty slate (offseason). NYY/NYM and CWS remain sits if present.
- Did not patch main. Did not merge. Did not rewrite p_home, locks, or ledgers. Did not dispatch workflow. Did not promote 2stackMLB. Did not start 24h watchdog.
- Champion remains KS1-LGB+dual-Poisson. 2stackMLB shadow-only.
- Next: safe 2stackMLB shadow research (walk-forward/failure taxonomy) only; no official train.

Do not merge without Kurt approval.
