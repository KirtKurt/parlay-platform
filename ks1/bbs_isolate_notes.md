# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-19 12:17Z EDT+4 / 12:17 UTC:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- Branch HEAD `daily.py` is PLACEHOLDER vs main. Do not merge PR 713.
- Latest schedule 35438863367 SUCCESS 10:59-11:05Z (~5m59s). Artifacts: ks1-daily / ks1-nightly / mlb-research-ingestion-35438863367.
- Next schedule slot ~11:59Z missing. Last attempt was schedule 10:59Z (~78m). HEALTH=CRON_GAP. Watchdog not started. Did not dispatch mlb-research-ingestion.yml.
- Latest dispatch 35441596008 SUCCESS 11:59-12:07Z (~7m43s). Artifacts: ks1-daily-35441596008, ks1-nightly-35441596008, mlb-research-ingestion-35441596008.
- Prior schedule 35425130291 FAILURE 05:53-05:57Z: hourly refresh / `ks1.daily --publish` failed; not unmatched BBS. Isolate-skip not the cause. No isolate code change.
- Latest push 35432151456 SUCCESS 08:29-08:36Z (PR #991).
- PR 710 open ready; SCHEMA adds p_lgb/pick_status/selection_reason. PR 711 draft shadow-only. PR 712 closed; watchdog not activated. PR 713 draft do-not-merge.
- Daily 35441596008: 15 rows, 0 confirmed / 15 projected; DET@CWS, PHI@NYM, NYY@ARI sits. BOS@TBR and SF@LAD projected_missing_starter.
- Nightly 35441596008: official Brier 0.2354 n=110 new_grades=0 status=no_new_final_grades locked_rows=110.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
