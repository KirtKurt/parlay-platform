# 2stackMLB shadow snapshot 2026-10-01

promoted: false
champion: KS1-LGB+dual-Poisson
attach: shadow-only
trained_official_model: false

Source: ks1-nightly-36952230309 report.json (run 36952230309). No ledger rewrite.

## Observed official ledger (not a challenger fit)
- status: no_new_final_grades
- new_grades: 0
- ledger_rows: 227
- official Brier: 0.235617813837353
- n: 227
- logloss: 0.6644707582726495
- pick accuracy: 0.5859030837004405 (133/227)
- mean_p_home: 0.5116598314869668
- home_win_rate: 0.5286343612334802
- admission locked_rows: 228; excluded 823490 no_bound_final
- calibration: deferred_to_next_nightly

## Failure taxonomy (one miss is not a retrain)
- Thin edge vs coin (coin Brier 0.25, skill 0.0144). Do not promote.
- Missing bound final 823490 blocks one locked row from grading.
- 2026-10-01 slate published 0 prediction rows; 849844 Live without a pre-T10 lock. Not a 2stack signal.
- Unmatched BBS events were isolated on main and did not fail the slate.

## Not done
- No walk-forward refit
- No official model train
- No KS1 p_home / lock / ledger write
- No SCHEMA change
- No merge
