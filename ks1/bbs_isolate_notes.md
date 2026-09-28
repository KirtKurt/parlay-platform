# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-28 05:14 EDT / 2026-09-28 09:14 UTC operator cycle:
- Isolate-skip already on main (`isolate_unmatched=True`, `missing_bbs_identity` in ks1/daily.py + tests/ks1_phase5/test_bbs_identity_isolation.py). This branch `ks1/daily.py` remains placeholder. Do not merge; do not patch main.
- HEALTH=OK. Latest schedule 36388806506 SUCCESS run 839 06:54:28Z-07:15:08Z (~20.7m). Latest completed dispatch 36396085187 SUCCESS run 841 08:13:31Z-08:24:09Z (~10.6m). Run 842 36402155043 IN_PROGRESS dispatch 09:13:49Z. Last attempt age <2m. Did not start 24h watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Artifacts on 36396085187: ks1-daily-36396085187, ks1-nightly-36396085187, ks1-loss-patterns-36396085187-1, mlb-research-ingestion-36396085187. Schedule 839 has matching ks1-daily/ks1-nightly artifacts.
- No live unmatched-BBS slate killer this cycle. Official dates[]=0 / BBS data count=0 for 2026-09-28.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs. 2stackMLB remains shadow-only; no official train.
- PR 710 open; SCHEMA/publish path still adds serving-gate fields. PR 711 draft shadow-only. PR 712 merged closed; watchdog not activated. PR 713 draft notes-only.
- TODAY SLATE 36396085187 date=2026-09-28: 0 rows, 0 confirmed / 0 projected, official_games=0, bbs_matched=0, exclusions=[]. Regular-season slate empty. NYY/NYM/CWS sits N/A this date. Prior sit 823490 remains no_bound_final on ledger admission.
- Nightly 36396085187: status=no_new_final_grades published=false new_grades=0 ledger_rows=227 official Brier=0.235618 n=227. ACCURACY on locked history; admission locked_rows=228 excluded 823490 no_bound_final. Calibration deferred_to_next_nightly.
- Bottleneck: empty 2026-09-28 official slate + isolate branch still placeholder. Next: wait next scheduled ingest; keep isolate notes current; no official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
