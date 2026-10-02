# 2stackMLB shadow failure taxonomy (2026-10-02)

Shadow only. Champion remains KS1-LGB+dual-Poisson (`KS1-LGB-326657a4edfd-DP-9abc4f415689`). `promoted=false`. No official model train. No p_home, lock, ledger, or SCHEMA rewrite.

Source: ks1-nightly artifact of run 36960697489 (schedule, success). Not a walk-forward fit.

## Official lock snapshot
- night_date: 2026-10-01
- locked_rows: 228; eligible_graded_rows: 227; excluded: game 823490 `no_bound_final`
- ledger_rows: 227; new_grades: 0; status: no_new_final_grades
- official Brier: 0.235617813837353; logloss: 0.6644707582726495; n: 227
- pick accuracy: 0.5859 (133/227); coin Brier 0.25; skill vs coin +0.0144
- mean_p_home: 0.5117; home_win_rate: 0.5286
- trained_LightGBM: false; authority_changed: false

## Taxonomy (one miss is not a retrain)
1. Favorite-fade misses: model rarely picks COL to win (picked_win_accuracy 0.0, n_picked small) while accuracy when fading COL is 0.8125. Shadow should not invert this into a COL longshot model.
2. Fade-failure on strong clubs: accuracy when fading LAD and SD is 0.0 on this sample (picked_against counts are small). Do not add a fade-the-favorite feature without a frozen challenger hash.
3. Home-rate gap: observed home win rate 0.529 vs mean p_home 0.512. Calibration remains temperature, fitted 2026-09-26, n=100, deferred this night. Do not refit from this tick.
4. Unbound final: 823490 excluded `no_bound_final`. Shadow attach must not grade that row.
5. Slate hole is not a model failure: daily 2026-10-01 published 0 prediction rows; official game 849844 excluded `not_scheduled_before_T10`; two unmatched BBS events isolated (88cf058e, a490caaf). Isolate-skip is already on main.

## Not done
- No walk-forward training.
- No shadow attach into serving.
- No promotion of 2stackMLB.
- NYY/NYM and CWS remain sits; this slate had no rows to bet.
