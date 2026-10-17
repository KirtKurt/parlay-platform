# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-10-17 12:30 UTC operator cycle:
- Main already implements isolate-skip via `bbs_assignments(..., isolate_unmatched=True)` and `missing_bbs_identity` exclusions.
- Latest scheduled runs (#1577 etc.) all success; no recent BBS identity failures killing slate.
- No CRON_GAP (last schedule ~1h ago, next expected soon).
- Did not patch main. Did not merge. Did not rewrite p_home, locks, or ledgers.
- Champion remains KS1-LGB+dual-Poisson. 2stackMLB shadow-only.

Do not merge without Kurt approval.

Previous: Status 2026-10-05 22:15 UTC: BBS 429 and Odds 401 both degrade. Truncation/schema/duplicate BBS stay hard errors. No p_home/lock/ledger rewrite. Not merged.
