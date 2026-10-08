# 2stackMLB shadow status 2026-10-08T12:25Z

promoted: false
champion: KS1-LGB+dual-Poisson
attached_to: official graded locks only as read-only shadow context
trained_official_model: false
ks1_p_home_locks_ledgers: untouched

## Source tick
- workflow run 37770341462 (schedule #1266) conclusion success
- nightly official_metrics brier 0.2367980082815068 n 241
- new_grades 0
- ledger_rows 241; admission locked_rows 242; excluded game 823490 reason no_bound_final
- pick_summary hits 140 / 241 accuracy 0.5809128630705395
- status no_new_final_grades; calibration_fitted false

## Failure taxonomy (shadow, one tick is not a retrain)
- no_new_grades: hourly tick sealed nothing new; do not refit
- no_bound_final: 823490 remains excluded from grading
- slate_identity: 2026-10-08 official game 849832 (CLE at CWS, if necessary) excluded missing_bbs_identity; unmatched BBS aaa8a466-25cd-4bf6-82e1-798dae058465 isolated; duplicate/schema/truncation still hard errors on main
- postseason_if_necessary sits: CWS fight and NYY/NYM remain sits; predictions.parquet rows 0; confirmed 0; projected 0; future_before_t10 849832

## Walk-forward
Not rerun this tick. Last official n=241 is the champion ledger, not a 2stackMLB fit. Next shadow walk-forward may read graded_ledger only. Do not write official p_home.
