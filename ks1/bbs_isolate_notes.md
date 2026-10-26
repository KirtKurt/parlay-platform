# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-10-26 04:15 UTC operator cycle:
- Latest scheduled run 40264926046 success (started 03:36Z, ~39min ago). No CRON_GAP.
- Prior run 40244517980 failed on main: BBS 429 in live_inputs.capture (ValueError). Branch degrades 429/NETWORK to empty catalogue so isolate-skip applies; Odds 401 already degrades. Main lacks this.
- PR 713 draft open (this branch). SCHEMA extended in daily.py (signal/starter/lineup profiles). Do not merge.
- PR 710 open (serving gates). PR 711 draft (2stackMLB shadow). PR 712 merged (watchdog; do not activate).
- ACCURACY=UNAVAILABLE (new_grades=0, ledger_rows=247 from nightly artifact).
- Slate: predictions.parquet empty (0 rows) for 2026-10-25. NYY/NYM and CWS remain sits.
- Did not patch main. Did not merge. Did not rewrite p_home, locks, or ledgers. Did not dispatch workflow. Did not promote 2stackMLB.
- Champion remains KS1-LGB+dual-Poisson. 2stackMLB shadow-only.

Do not merge without Kurt approval.
