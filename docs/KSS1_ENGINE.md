# KSS1 goals engine

Shipped 12 September 2026 on `soccer_auto` as isolated modules.

## Modules

- `soccer_auto/kss1_markets.py` — Dixon-Coles score matrix and 1X2 / DC / O/U 2.5 / BTTS
- `soccer_auto/kss1_identity.py` — Odds API ↔ BBD mapping; refuse ambiguous joins
- `soccer_auto/kss1_bbd.py` — BBD client (`sport=football`, UUID ids only). Odds stay on The Odds API
- `soccer_auto/kss1_lock.py` — T-60 public engine contract, T-45 training, postponement void
- `soccer_auto/kss1_engine.py` — `predict_match()` shadow engine

## Authority

`SHADOW_LEARNING`. `automatic_prediction_allowed=False`.

Existing soccer_auto public bind remains **T-10** until inference contract tests are cut over. The engine already enforces T-60 for KSS1 picks.

## Not in this drop

- Promotion out of shadow
- Changing `PUBLICATION_CUTOFF_MINUTES`
- Auto-betting
- LLM picks

## Review repair and remaining model qualification

The follow-up to PR #853 repairs all eight reported cases: tier-specific goals
market eligibility, first-native-bind immutability, missing provider kickoff
evidence, explicit zero inputs, equivalent timestamp serialization, transport
timeouts, double-chance selection, and supported BBD xG response shapes.
The double-chance confidence threshold is independently set to 0.70, above
the mathematical 2/3 floor of the largest pair. This is a shadow selection
rule, not an empirically fitted threshold or an accuracy claim.

Predictions now include `input_coverage`: `defaulted_fields`,
`team_strength_complete`, and `xg_complete`. These describe supplied numeric
values only; they are not source/provenance certification. Missing fields still
use the existing shadow baseline and remain visibly defaulted.

The current `kss1_runtime.build_kss1_shadow_item()` passes fixture identity,
observation time, and optional market prior only. It does not pass attack,
defence, league-rate, or xG inputs. The scheduled `soccer_auto.trainer` trains
the separate market-feature model; it does not fit this goals engine. Neither
the BBD parser repairs nor a successful freeze invocation closes that gap.

Before qualification, the goals engine needs historical team inputs with
receipts proving their availability before the prediction cutoff, a shared
training/serving feature contract, chronological out-of-sample evaluation,
and persisted prospective grades. Same-match observed xG from live/final
match statistics must not be used as a pregame feature. Prior-match xG needs
point-in-time aggregation and provenance before connection to this engine.

The test-only KSS1 Shadow Contract workflow runs the full soccer suite on the
PR head. It has no deployment or AWS credentials. This repair does not change
the T-10 public bind, promote a model, or qualify the shadow books for public
publication.
