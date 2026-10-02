# 2stackMLB shadow failure taxonomy (2026-10-02 15:20 UTC)

Champion remains KS1-LGB+dual-Poisson. This note is shadow-only. promoted=false. No official train. No p_home, lock, or ledger rewrite.

Source: ks1-nightly-37020440888 (schedule run 37020440888, success). status=no_new_final_grades. published=false. new_grades=0. locked_rows=228. eligible=227. excluded 823490 no_bound_final. official Brier=0.235618 logloss=0.664471 n=227. pick accuracy=0.5859 (133/227).

Taxonomy (one miss is not a retrain):
- Off-day empty slate 2026-10-02: official_games=0, prediction rows=0, bbs_identity_exclusions=[]. Not a model failure.
- Single against-pick misses (Dodgers model_accuracy_when_against=0 on picked_against=1) are sample-size noise, not a feature swap.
- Thin home edge: mean_p_home=0.512 vs home_win_rate=0.529. Skill vs coin Brier 0.014. Do not promote 2stackMLB from this audit.
- Open hole 823490 stays unbackfilled. Do not invent a bound final.

Next shadow work: chronological walk-forward on the existing graded ledger only. Do not attach to official publish.
