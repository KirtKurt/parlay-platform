# 2stackMLB

Uncorrelated third engines on top of frozen KS1. This package has **no
authority to rewrite** official `p_home`, model hashes, S3 prediction
rows, or the KS1 calibration contract.

KS1 stays two engines: LightGBM winner + dual-Poisson runs. 2stackMLB
adds the engines that fail on different games, then gates tickets when
those engines fight.

## Contract

| Field | Who owns it | Mutable? |
|---|---|---|
| `p_home` / `p_home_official` | KS1 LightGBM (raw until Platt) | No |
| `lambda_home`, `lambda_away` | KS1 dual Poisson | No |
| `p_stack` | 2stackMLB | Yes, shadow only |
| `pick_status` | 2stackMLB | `bet` / `shrink` / `pass` |
| `selection_reason` | 2stackMLB | Diagnostic |

A second LightGBM with a different seed is not a different algorithm.
2stackMLB refuses to add one.

## Engines

1. **Pitcher-adjusted Elo** (`elo.py`)
   Team Elo + starter Elo, home field = 30 points, K_team = 6, K_pitcher = 8,
   log-damped margin. A 10-3 against Colorado moves the Yankees ~3 points.
   If Elo, Poisson, and market agree and LGB is the lone outlier, LGB loses
   the vote.

2. **Poisson-lambda as residual features** (`poisson_bridge.py`)
   Does not refit KS1 Poisson. Emits `lambda_diff`, `log_lambda_ratio`,
   `p_poisson`, `poisson_minus_market`, `lgb_minus_poisson`. A future
   challenger LGB may consume these. Production LGB may not, until a
   frozen hash and a reliability chart say so.

3. **Inning Markov** (`markov.py`)
   Starter innings first, bullpen lambda after, walk-off in the 9th, extra
   innings one frame at a time. Advisory: can tighten `bet` to `shrink`,
   cannot loosen a `pass`.

4. **Market-as-prior Bayesian update** (`bayes_market.py`)
   logit(p) = logit(market) + kappa * (logit(signal) - logit(market)), then
   clip to +/-8pp of market. This is how 77% becomes a number you can
   publish without betting it.

5. **Ridge logistic** (`glm.py`)
   Same five stack features. If the GLM cannot recover the LGB edge
   within 8pp, the interaction did not replicate. Diagnostic only.

## Gate

Order is fixed.

1. Unverified starter -> `pass`.
2. Poisson, Elo, and market must agree on side. Otherwise `pass`.
   LGB can join them; it cannot veto them.
3. If {LGB, Poisson, market} span more than 8pp, status is `shrink`
   and `p_stack` is pulled toward market.
4. Markov / GLM may only tighten.

`p_home_official` is copied through unchanged in every branch.

## Tonight, 2026-09-11

| Game | LGB | Poisson | Market (devig) | Rule |
|---|---|---|---|---|
| NYM @ NYY (-142) | 0.77 | 0.54 | ~0.59 | sit / shrink. 23pp fight. |
| CWS @ STL (CWS -112) | CWS 0.64 | CWS 0.44 | ~0.53 | sit. Side split. |

NYY does not get a pass because they won 10-3 last night. That 10-3 is
part of why LGB is at 77.

## Isolation

- Package import path: `stack2mlb`
- Display name: `2stackMLB`
- Does not import serving write paths from `ks1.daily` or `ks1.publish`
- Does not change `ks1/model_refs.json`
- Draft PR only. No production promotion.
