# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-11-27 17:32 UTC operator cycle:
- Latest scheduled run 46040994299 success (started 17:20:04Z, completed ~17:29Z, ~9min). Previous 46032885334 success. Artifacts present: ks1-daily-46040994299, ks1-nightly-46040994299, ks1-loss-patterns. No CRON_GAP. Recent schedule runs all success. No recent failures requiring isolate-skip.
- Isolate-skip already committed on this branch (isolate_unmatched=True in daily.py). bbs_degrade present. PR 713 draft open. Do not merge without approval.
- PR 710 open non-draft (serving gates, SCHEMA extensions for p_lgb/pick_status). PR 711 draft (2stackMLB shadow-only). PR 712 closed/merged (watchdog; do not activate).
- ACCURACY available: official Brier 0.2375, n=250, new_grades=0. Status no_new_final_grades. locked_rows=251. ACCURACY=AVAILABLE (not n=0).
- Slate: predictions.parquet 0 rows (offseason / no official games). official_games=0. NYY/NYM and CWS remain sits if present.
- Did not patch main. Did not merge. Did not rewrite p_home, locks, or ledgers. Did not dispatch workflow. Did not promote 2stackMLB. Did not start 24h watchdog.
- Champion remains KS1-LGB+dual-Poisson. 2stackMLB shadow-only.
- Next: safe 2stackMLB shadow research (walk-forward/failure taxonomy) only; no official train. Highest-value safe repair remains documenting health; isolate already done.

Do not merge without Kurt approval.
