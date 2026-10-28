# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-10-28 13:30 UTC operator cycle:
- Latest scheduled run 40695750672 in_progress (started 13:21Z). Prior success 40688159249 (12:29Z, ~13min duration). No CRON_GAP.
- Prior fails 40601351281 / 40594027074 on main: BBS 429 in live_inputs.capture (ValueError provider capture failed). Branch degrades 429/NETWORK/BBS_API_KEY_MISSING to empty catalogue so isolate-skip applies; Odds 401 already degrades. Main lacks this.
- PR 713 draft open (this branch). SCHEMA extended in daily.py (signal/starter/lineup profiles). Do not merge.
- PR 710 open (serving gates). PR 711 draft (2stackMLB shadow). PR 712 merged (watchdog; do not activate).
- ACCURACY=UNAVAILABLE (new_grades=0, ledger_rows=249 from nightly artifact).
- Slate: predictions.parquet 1 row (Rays vs Brewers, game 849799, confirmed_lineups=0). NYY/NYM and CWS remain sits.
- Did not patch main. Did not merge. Did not rewrite p_home, locks, or ledgers. Did not dispatch workflow. Did not promote 2stackMLB.
- Champion remains KS1-LGB+dual-Poisson. 2stackMLB shadow-only.

Do not merge without Kurt approval.
