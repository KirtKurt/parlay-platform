# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-11-06 12:23 UTC operator cycle:
- Latest scheduled run 42209432719 completed success (started 11:19:50Z, duration ~10min). Artifacts: ks1-daily-42209432719, ks1-nightly-42209432719. No CRON_GAP (hourly ~:19-21). In-progress workflow_dispatch also present.
- No current failure. Isolate-skip already committed on this branch (daily.py bbs_assignments isolate_unmatched, bbs_degrade for 403/429). PR 713 draft open. Do not merge without approval.
- PR 710 open (serving gates). PR 711 draft (2stackMLB shadow-only). PR 712 closed/merged (watchdog; do not activate).
- ACCURACY available: official Brier 0.2375, n=250, new_grades=0 from ks1-nightly-42209432719.
- Slate: predictions.parquet / csv 0 rows, official_games=0, confirmed_lineups=0, projected=0. Empty slate (offseason). NYY/NYM and CWS remain sits if present.
- Did not patch main. Did not merge. Did not rewrite p_home, locks, or ledgers. Did not dispatch workflow. Did not promote 2stackMLB. Did not start 24h watchdog.
- Champion remains KS1-LGB+dual-Poisson. 2stackMLB shadow-only.

Do not merge without Kurt approval.
