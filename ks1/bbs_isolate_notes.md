# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-10-20 15:20 UTC operator cycle:
- Main runs failing on BBS 429 + Odds 401 in live_inputs.capture (raises ValueError if any errors).
- Branch ks1-isolate-bbs-identity-20260911 already degrades BBS 429/NETWORK to empty catalogue via bbs_degrade.degraded_bbs_capture so isolate-skip applies; Odds 401 already degrades.
- No CRON_GAP (latest schedule attempt ~15:10 UTC, within 70min).
- PR 710 open (serving gates, SCHEMA changes). PR 711 draft (2stackMLB shadow). PR 712 merged (watchdog; do not activate).
- Did not patch main. Did not merge. Did not rewrite p_home, locks, or ledgers. Did not dispatch workflow.
- Champion remains KS1-LGB+dual-Poisson. 2stackMLB shadow-only.
- ACCURACY=UNAVAILABLE (no new graded_ledger inspected; last success had nightly artifact).
- Slate: daily failed this hour; prior success had predictions. NYY/NYM and CWS remain sits.

Do not merge without Kurt approval.
