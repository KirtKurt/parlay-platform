# 2stackMLB shadow failure taxonomy (2026-10-09)

Champion remains KS1-LGB+dual-Poisson. This note is shadow-only. `promoted=false`. No official model was trained. No p_home, lock, or ledger rewrite.

Source: ks1-nightly artifact on run 37900289890 (main, success). official_metrics brier 0.2367980082815068, logloss 0.6668646297093749, n=241. new_grades=0. locked_rows=242 with one exclusion (game 823490, no_bound_final).

## Taxonomy (one miss is not a retrain)

- Thin edge: pick accuracy 0.5809 (140/241), brier skill vs coin 0.0132. Not a promotion signal.
- Favorite-fade miss class: Colorado Rockies picked to win 1/17, that pick missed (picked_win_accuracy 0.0). Model was right when fading them (against-accuracy 0.8125). Do not add a Rockies-specific official feature.
- Side-selection miss class: New York Mets picked_win_accuracy 0.143 (1/7). NYY series is already over (TB 3-0); NYM/NYY remain sits on any residual slate rows.
- CWS still alive in ALDS but no official game on 2026-10-09. Sit remains. Next official game is 2026-10-10 CWS at CLE if needed.
- Empty 2026-10-09 official schedule (statsapi sportId=1 dates count 0) matches the off day. BBS isolate-skip already on main recorded one unmatched BBS id e53688da-cc6b-4175-8bcb-a47c2766a26c and did not fail the slate.

## Not done

- No walk-forward refit.
- No shadow attach into KS1 publish path.
- No SCHEMA change.
- Do not merge.
