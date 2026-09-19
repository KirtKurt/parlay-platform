# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-19 05:08Z:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- Branch HEAD `daily.py` was PLACEHOLDER; isolate behavior is already live on main. Do not merge PR 713.
- Latest schedule 35409065933 success ~6m05s artifacts ks1-daily-35409065933 + ks1-nightly-35409065933.
- Latest attempt dispatch 35421151025 success ~6m44s artifacts ks1-daily-35421151025 + ks1-nightly-35421151025.
- No CRON_GAP vs last attempt 04:23Z. Watchdog not started. PR 712 watchdog not activated.
- BBS isolate live: unmatched `28075fbf-1319-4bbf-a84e-2a8e1019f024` skipped; official 824545 excluded `missing_bbs_identity` (slate continued).
- Nightly 2026-09-18: official Brier 0.241 n=95 new_grades=0. locked_rows=106; 11 waiting no_bound_final. Latest nightly 35421151025 status=not_due_or_already_completed.
- Slate 2026-09-19: 14 scored rows, 0 confirmed / 14 projected; official_games=15. NYY@ARI and PHI@NYM sits. CWS off slate.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
