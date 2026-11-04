# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-11-04 08:21 UTC operator cycle:
- Latest scheduled run 41791642884 completed failure (started 07:21Z, duration ~18min). Prior fails also BBS 403/429. No CRON_GAP (runs hourly ~:21).
- Failure: live_inputs.capture ValueError provider capture failed on BBS 403 (Odds 401 already degraded). Branch degrades 403/429/NETWORK/BBS_API_KEY_MISSING to empty catalogue so isolate-skip applies. Main only degrades 429. SCHEMA extended in daily.py. Do not merge.
- PR 713 draft open (this branch). PR 710 open (serving gates). PR 711 draft (2stackMLB shadow). PR 712 closed/merged (watchdog; do not activate).
- ACCURACY=UNAVAILABLE (new_grades=0, ledger_rows=250 from nightly artifact ks1-nightly-41791642884).
- Slate: no predictions.parquet this hour (daily failed before publish). NYY/NYM and CWS remain sits.
- Did not patch main. Did not merge. Did not rewrite p_home, locks, or ledgers. Did not dispatch workflow. Did not promote 2stackMLB.
- Champion remains KS1-LGB+dual-Poisson. 2stackMLB shadow-only.

Do not merge without Kurt approval.
