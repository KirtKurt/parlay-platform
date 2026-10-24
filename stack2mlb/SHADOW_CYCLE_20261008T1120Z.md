# 2stackMLB shadow cycle 2026-10-08T11:20Z

Champion remains KS1-LGB+dual-Poisson. 2stackMLB is shadow-only. promoted=false.
No official model trained. No p_home, lock, or ledger rewrite. No merge.

Source: nightly artifact ks1-nightly-37764018194 (run 37764018194, success).

## Official ledger (read-only)
- Brier 0.236798, logloss 0.666865, n=241 locked graded rows
- new_grades=0, status=no_new_final_grades
- accuracy 0.5809 (140/241)

## Failure taxonomy (loss_trace, non-holdout)
- analyzed 193, wins 116, losses 77, loss_rate 0.399
- frozen holdout excluded 48
- missing lineup_bullpen_profile surface on 48 rows
- starter group is the dominant decision influence on sampled rows (~75%)
- one miss is not a retrain; no shadow attach this tick because today's published slate has 0 scored rows (game 849832 excluded missing_bbs_identity)

## Not done
- Did not promote 2stackMLB
- Did not dispatch mlb-research-ingestion.yml
- Did not activate the PR 712 watchdog
