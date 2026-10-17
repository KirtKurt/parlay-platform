# 2stackMLB failure taxonomy — 2026-10-06T19:14Z

Shadow only. promoted=false. No official model trained. No KS1 p_home, lock, or ledger rewrite. No merge. No ingestion dispatch. PR 712 watchdog not activated.

## Ingest
- Latest schedule run 37512346812 failure. Started 2026-10-06T18:34:25Z, updated 18:42:29Z (~8m). Cron gap false (last attempt start <70m before 19:14Z).
- Artifacts: ks1-nightly-37512346812 present; ks1-daily missing (capture aborted). mlb-research-ingestion-37512346812 present.
- Cause: BBS HTTP 429 in ks1.live_inputs (`provider capture failed`). Odds 401 already degraded. Not unmatched/ambiguous BBS identity.
- Isolate-skip already on main (`isolate_unmatched=True`). It never ran because capture aborted first.
- 429 degrade remains unmerged on draft PRs 1116/1117/1118. Do not open another copy. Do not patch main.

## Official champion (nightly artifact, read-only)
- Brier 0.2353774129257362, logloss 0.6639793672853979, n=235, new_grades=0
- locked_rows=236, eligible_graded_rows=235, status=no_new_final_grades
- exclusion: 823490 no_bound_final

## Slate (last success daily 37464913690, date=2026-10-06)
- rows=2, confirmed=0, projected=2, bbs_matched=2, exclusions=[]
- LAD@ATL 849819 projected, both starters probable, market unavailable
- MIL@SD 849826 projected, both starters probable, market unavailable
- NYY/NYM and CWS not on slate (sits)

## Gate
One zero-grade hour is not a retrain. Do not promote 2stackMLB.
