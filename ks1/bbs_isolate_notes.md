# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-10-01 13:19 EDT / 2026-10-01 17:19 UTC operator cycle:
- Isolate-skip already on main (`isolate_unmatched=True`, `missing_bbs_identity` in ks1/daily.py + tests/ks1_phase5/test_bbs_identity_isolation.py). This branch `ks1/daily.py` remains placeholder. Do not merge; do not patch main.
- HEALTH=FAIL_ODDS_AUTH, not CRON_GAP. Latest schedule 36892414457 FAILURE run 998 16:29:42Z-16:37:22Z (~7.7m). Cause: Odds API 401 in ks1.live_inputs capture, not unmatched BBS. Dispatch 36897895393 IN_PROGRESS 17:14:18Z. Last schedule age ~50m. Did not start 24h watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Artifacts on 36892414457: ks1-nightly-36892414457, ks1-loss-patterns-36892414457-1, ks1-input-identities-36892414457, mlb-research-ingestion-36892414457. ks1-daily missing (publish never started).
- No live unmatched-BBS slate killer. Official date=2026-10-01 games=1 (849844 PHI@ATL Preview). BBS data count=3 including PHI@ATL plus 2026-10-02 UTC Cubs@SD and BOS@NYY.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs. 2stackMLB remains shadow-only; no official train.
- PR 710 open non-draft; SCHEMA/publish path still adds p_lgb/pick_status/selection_reason. PR 711 draft shadow-only. PR 712 merged closed; watchdog not activated. PR 713 draft notes-only. PR 1111 draft odds-auth degrade (do not merge).
- TODAY SLATE: predictions.parquet not published this run. Official 1 scheduled, confirmed/projected unavailable. NYY/NYM and CWS remain sits. BOS@NYY is BBS-only on this capture.
- Nightly 36892414457: status=no_new_final_grades published=false new_grades=0 ledger_rows=227 official Brier=0.235618 n=227. Admission locked_rows=228 excluded 823490 no_bound_final. Calibration deferred_to_next_nightly.
- Bottleneck: Odds API 401 aborts capture before daily publish. Next: keep PR 1111 draft; do not merge; no official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
