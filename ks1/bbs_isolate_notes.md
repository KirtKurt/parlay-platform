# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-10-02 14:15 EDT / 2026-10-02 18:15 UTC operator cycle:
- Isolate-skip already committed on main (`bbs_assignments(..., isolate_unmatched=True)`; unmatched continue; official games without BBS become `missing_bbs_identity`). This branch `ks1/daily.py` remains a one-line placeholder that would delete the publisher. Do not merge. Do not patch main. Did not restore daily.py (branch is 114 commits off main; a restore here is not a safe same-tree repair).
- HEALTH=OK, not CRON_GAP. Latest schedule 37040785864 SUCCESS run 1050 17:27:04Z-17:36:44Z (~9.7m). Prior schedule 37034422862 SUCCESS 16:30:40Z. Last schedule age ~48m at check. Later dispatch 37043296847 SUCCESS run 1051 17:49:14Z-17:59:08Z (~10m). Did not start 24h watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Artifacts on 37040785864 and 37043296847: ks1-daily, ks1-nightly, ks1-loss-patterns-1, mlb-research-ingestion. No failed run this tick. exclusions=[] bbs_identity_exclusions=[].
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs. No official train.
- PR 710 open non-draft; SCHEMA/publish path still adds p_lgb/pick_status/selection_reason and merges missing locks. PR 711 draft shadow-only. PR 712 merged 2026-09-11; watchdog not activated. PR 713 draft; head still placeholder.
- TODAY SLATE from dispatch 37043296847: date=2026-10-02 predictions rows=0 confirmed=0 projected=0 official_games=0. NYY/NYM and CWS remain sits. publication readback_verified=true write_keys=[]. model KS1-LGB-326657a4edfd-DP-9abc4f415689.
- Nightly 37043296847: status=no_new_final_grades published=false new_grades=0 ledger_rows=227 locked_rows=228 eligible=227 excluded 823490 no_bound_final. official Brier=0.235618 logloss=0.664471 n=227. as_of 2026-10-02T17:53:59Z.
- Bottleneck: Oct 2 empty official slate; 823490 unbound final. Next: do not backfill p_home; do not merge 710/713.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
