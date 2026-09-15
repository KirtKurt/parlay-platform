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

Run 34871343076 (14 Sep 2026) classified 19 missing T-10 locks. All 19 were
on-time (`late_discovered_due_events=0`); none may be backfilled after cutoff.
The 90-second T10 capture window sat inside a 300-second collection cadence, so
an in-flight coverage plan burned the only freeze tick. Collection now uses a
60-second cadence for the last ~21 minutes and forces a burst when T10 is open
and discovery is not `HTTP_200`. Freeze scans 60 minutes ahead and processes
open T10 events before T45 S3 loads. `PUBLICATION_CUTOFF_MINUTES` is unchanged.

Deploy Isolated Soccer Auto splits KSS1 trainer/readback (`verify-and-deploy`)
from champion T-10 health (`prove-isolated-runtime`). The health contract is
still `soccer-auto-health-proof-v1`; the assert is not dropped. A
`DEGRADED_INTEGRITY` proof fails the second job only.

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

The goals-training integration adds a separate scheduled
`soccer_auto.kss1_training_runtime.trainer_handler`. The existing
`soccer_auto.trainer` still trains its own market-feature model. The goals
trainer uses the existing isolated soccer role and artifact bucket and writes
only `MODEL#kss1_goals#global / SHADOW_CONTEXT`; it never writes a champion.

## Historical inputs and serving

`kss1_features.py` builds the same digest-bound features for retrospective
training and live shadow inference. It uses the preceding 365 days in the
same competition, with each team's last 20 games, five-game prior shrinkage,
league home/away scoring rates, and relative attack/defence strengths.
Prior-match xG gets its own aggregates and explicit missing flags/counts.
At least five prior games per team are required for training eligibility.

The production history comes from admissible, signed final settlements.
Conflicting events are excluded. Final-score availability includes the actual
receipt and admissibility-certificate timestamps. BBD enrichment preserves
the independently observed xG receipt in S3, using an unambiguous team,
competition, kickoff and UUID mapping. Newly collected historical statistics
are timestamped at collection; their availability is never backdated. No
same-match final statistics enter pregame features.

The optional existing `BBD_API_KEY` deployment secret is stored in an isolated
Secrets Manager secret. Only its ARN enters the new goals Lambda's environment;
the existing soccer role receives GetSecretValue access to that exact secret.
Each run queries at most one match list per competition
and 20 mapped stats requests, reuses retained receipts, and records missing
credentials, missing xG, mapping failures and provider errors. Persisted attempt
timestamps rotate failed/missing observations behind unattempted games, so
permanent gaps cannot consume every run's stats budget. This bounded
recent-match endpoint does not provide a complete historical xG backfill.
Without usable xG, the goals-only candidate can train; xG coverage is never
invented. Retained production history is capped at 5,000 matches.

The freeze wrapper reads the persisted context once, scans upcoming events
through 90 minutes, and writes learned shadows only on or before T−60. The
context must already exist at that cutoff. It retains the actual model
digest, feature receipts and immutable prediction identity. Large feature
receipts are stored in S3. Research histories/models are rejected by live
inference. Missing contexts retain the visibly defaulted legacy shadow.

## Fitting and validation

`kss1_goals_model.py` fits independent regularized Poisson home/away rates
with league-rate offsets. It compares goals-only and goals-plus-xG candidates
at three regularization strengths. Chronological train/validation/holdout
boundaries use approximately 60/20/20 percent of eligible rows with two-day
embargoes and label-availability checks. Validation log loss selects the
candidate; only pre-holdout rows enter its final fit. Held-out predictions
use exactly the serialized model's serving calculation, without market-odds
blending. Earlier holdout results can enter later holdout features only after
their receipt timestamps, as in sequential forecasting.

Reports include split boundaries, event/feature/label manifests, 1X2 Brier
score and log loss, accuracy, O/U 2.5, BTTS and double-chance metrics, xG
coverage and fitted feature contributions. The comparison baseline is the
untrained 1.45/1.15 goals grid on identical games, not the incumbent market
model. The scheduled path requires at least 500 initial training games and
100 each for validation and holdout. Insufficient data is explicitly reported
as untrained. A candidate that fails to lower Brier without worsening log
loss cannot replace the previous shadow model.

The independent schedule runs every six hours at `01:43, 07:43, 13:43, 19:43`
UTC after deployment. `GET /v1/soccer-auto/kss1` returns the latest training
pointer and blockers. Adding this code does not itself deploy or invoke the
Lambda. Prospective goals grades and comparison against the incumbent market
model are still required before any public qualification.

## Recorded live shadow picks

`GET /v1/soccer-auto/kss1/picks?date=2026-09-14&selection=12`
reads persisted predictions for the requested America/New_York calendar day.
Omit the date for today and omit selection to return all recorded books. The
default requires a fitted goals model and an immutable on-or-before-T60 record
matching the current fixture revision, teams and kickoff. `selection=12`
returns actual threshold-qualified no-draw selections, not every fixture's
no-draw probability. Missing forecasts are listed with explicit reasons.
The response remains SHADOW_LEARNING and never promotes a model. For diagnosis,
`trained_only=false` additionally exposes explicitly labelled untrained baselines.
The deployment workflow invokes the separate goals trainer and verifies both
the status and picks API handlers, retaining their results in its run summary.
Successful infrastructure verification does not imply enough history to train,
nonempty picks, or prospective qualification; those states remain explicit.

## Reproducible offline research

Use `python scripts/train_kss1_goals.py --history history.json --out results`
for canonical receipt-bearing history. Output includes the full training
table, validation report and fitted model. The CLI has smaller exploratory
minimums (100 training and 50 each validation/holdout).

`scripts/kss1_open_data_research.py --commit <40-character-SHA> --out history`
can prepare the four 2015/16 major-league seasons in StatsBomb Open Data for
non-commercial research. It records source hashes and actual archive retrieval
times, marks date-only two-day availability assumptions as
`DATE_ASSUMED_RESEARCH`, and requires `--allow-research` in the training CLI.
Such results do not prove point-in-time production validity and cannot serve
live. Follow the source's terms and attribution requirements; do not commit
downloaded data or research model weights to this repository.

The test-only KSS1 Shadow Contract workflow runs the full soccer suite on the
PR head. It has no deployment or AWS credentials. This repair does not change
the T-10 public bind, promote a model, or qualify the shadow books for public
publication.
