# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-20 09:11 EDT / 2026-09-20 13:11 UTC:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- Branch notes only. Do not merge. Do not patch main. Do not replace `ks1/daily.py` on this branch.
- Latest schedule: 35510420048 SUCCESS 12:22:21Z-12:30:54Z (~8.5m). Artifacts ks1-daily-35510420048, ks1-nightly-35510420048.
- Prior schedule: 35493828801 SUCCESS 06:17:07Z. Gap to next schedule ~6h filled by dispatches/pushes; latest schedule age ~49m at check. HEALTH=OK. No CRON_GAP. Watchdog not started.
- Failed pushes 35496967044 / 35497262018: settled-loss / lineup-proof path — not BBS identity. No isolate code patch this hour.
- Did not dispatch mlb-research-ingestion.yml. Did not activate PR 712 watchdog. Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged; watchdog not activated. PR 713 draft isolate notes; daily.py on this branch is not the merge candidate.
- Daily 35510420048 date=2026-09-20: 15 rows, 0 confirmed / 15 projected (2 projected_missing_starter: CIN vs CHC, LAD vs SF). NYY@ARI and DET@CWS sit. NYM home vs PHI sit.
- Nightly 35510420048: official Brier 0.2353 n=125 new_grades=0 status=no_new_final_grades locked_rows=125. Calibration deferred.
- 2stack research withheld from merge; isolate already on main so shadow walk-forward remains PR 711 only.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
