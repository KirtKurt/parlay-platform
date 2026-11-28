# 2stackMLB shadow status (not promoted)

Cycle: 2026-11-16T10:19Z operator hour.
Champion remains KS1-LGB+dual-Poisson. This file does not change p_home, locks, ledgers, or SCHEMA.

Latest scheduled main ingest run 43840070485:
- status: completed success
- duration: ~11min
- artifacts: ks1-daily-43840070485, ks1-nightly-43840070485

Official metrics (from retained graded_ledger pattern):
- new_grades: 0
- ledger_rows / n: 250
- official Brier: ~0.2375 (n=250)
- ACCURACY available

Today slate (2026-11-16): 0 rows (offseason). confirmed=0 projected=0.
- NYY/NYM and CWS remain sits

BBS isolate-skip already on main (via prior #953). PR 713 remains draft/placeholder. No CRON_GAP. No watchdog activation. No official model trained. No promotion.
Next: continue shadow walk-forward / failure taxonomy only.
