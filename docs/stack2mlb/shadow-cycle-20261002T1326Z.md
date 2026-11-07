# 2stackMLB shadow cycle 2026-10-02 13:26Z

Champion remains KS1-LGB+dual-Poisson. This note is shadow-only. promoted=false. No official model train. No p_home, lock, or ledger rewrite. No ingestion dispatch.

## Live KS1 observed (run 37008176573)
- official Brier 0.235617813837353, logloss 0.6644707582726495, n=227 locked graded rows
- new_grades=0, status=no_new_final_grades
- today slate rows=0 (predictions.csv header only; official_games=0). No NYY/NYM or CWS rows to sit.
- one admission exclusion: game 823490 no_bound_final (not rewritten)

## Shadow research
Walk-forward and failure taxonomy stay on this branch. Empty 2026-10-02 slate gives no new attach rows. Do not fit a second GBDT. Do not publish shadow scores into KS1 predictions.

## Next
Re-run shadow attach only when a non-empty predictions.parquet exists. Isolate BBS repair stays on ks1-isolate-bbs-identity-20260911 / PR 713 draft; do not merge.
