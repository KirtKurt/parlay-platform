# 2stackMLB shadow taxonomy (2026-10-05)

Shadow only. Champion remains KS1-LGB+dual-Poisson. No official model was trained. `p_home`, locks, and ledgers were not rewritten. `promoted=false`.

Source: nightly artifact `ks1-nightly-37260457113` / `graded_ledger.json` (run 37260457113). Official metrics copied, not recomputed into the ledger.

- official Brier 0.23532102537089594
- n graded 233
- locked rows 234
- excluded 823490 `no_bound_final`
- new_grades this catch-up 1
- raw_model_version KS1-LGB-326657a4edfd-DP-9abc4f415689

## Calibration by p_home decile (home win)

| bin | n | mean p | home rate | brier |
| --- | --- | --- | --- | --- |
| 0.2-0.3 | 8 | 0.283 | 0.000 | 0.080 |
| 0.3-0.4 | 33 | 0.367 | 0.273 | 0.206 |
| 0.4-0.5 | 62 | 0.455 | 0.581 | 0.262 |
| 0.5-0.6 | 84 | 0.545 | 0.560 | 0.248 |
| 0.6-0.7 | 31 | 0.640 | 0.710 | 0.218 |
| 0.7-0.8 | 13 | 0.742 | 0.769 | 0.165 |
| 0.8-0.9 | 2 | 0.837 | 0.000 | 0.700 |

Underdog bins 0.4-0.5 are miscalibrated (home won 58% when mean p was 0.455). The two 0.8+ favorites both lost (824867, 824543). One miss is not a retrain.

## Worst settled misses (not a retrain signal)

- 824867 p_home 0.840 home_win 0 (0-4)
- 824543 p_home 0.834 home_win 0 (6-9)
- 824866 p_home 0.727 home_win 0 (6-7)
- 824711 p_home 0.705 home_win 0 (2-3)
- 824139 p_home 0.701 home_win 0 (2-4)

Next shadow step: walk-forward attach of Elo/Markov against these rows. Do not promote 2stackMLB.
