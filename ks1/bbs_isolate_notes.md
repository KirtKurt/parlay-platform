# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-20 12:04 EDT / 2026-09-20 16:04 UTC:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- Branch notes only. Do not merge. Do not patch main. Do not replace `ks1/daily.py` on this branch (PR tree is not a merge candidate).
- Latest schedule: 35510420048 SUCCESS 12:22:21Z-12:30:54Z (~8.5m). No later schedule by 16:04Z. HEALTH=CRON_GAP. Watchdog not started. Did not dispatch mlb-research-ingestion.yml.
- Latest main ingest: 35519458026 workflow_dispatch SUCCESS 15:24:06Z-15:29:38Z (~5.5m). Artifacts ks1-daily-35519458026, ks1-nightly-35519458026.
- Prior dispatch: 35516352986 SUCCESS 14:23:47Z. Failed dispatch 35513338999 13:23:04Z exit 134 SIGABRT in `ks1.daily --publish` (not BBS identity). Later runs recovered.
- Did not activate PR 712 watchdog. Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged; watchdog not activated. PR 713 draft isolate notes only.
- Daily 35519458026 date=2026-09-20: 15 rows, 6 confirmed / 9 projected (1 projected_missing_starter: SF@LAD). NYY@ARI and DET@CWS sit. PHI@NYM confirmed sit per fight rule.
- Nightly 35519458026: official Brier 0.2353 n=125 new_grades=0 status=no_new_final_grades locked_rows=125. Calibration deferred.
- 2stack research withheld from merge; isolate already on main so shadow walk-forward remains PR 711 only.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
