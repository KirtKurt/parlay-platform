# KS1 Phase 5: lineup and starter refresh

KS1 now reuses unchanged prediction rows, including their original `as_of`,
instead of rescoring the entire upcoming slate on every hourly capture. An
observed starter or lineup change rebuilds that game. The same date's Parquet
object is conditionally replaced; other date prefixes remain untouched.

This change is stacked on Phase 4 (#700). It does not merge or deploy the
pending Phase 2–4 stack, change R8, retrain models, or start Phase 6.

## Execution and sources

The existing `.github/workflows/mlb-research-ingestion.yml` hourly minute-23
trigger captures fresh inputs and calls `ks1.daily`, which now performs the
selective refresh automatically. No second scheduler, resource, AWS job, or
secret is added. The PR verification job has no AWS/provider credentials.

BBS still supplies match identities; The Odds API is still fetched once per
capture. Existing history and model artifacts are reused. The current BBS
match rows do not contain batting orders and its stored lineup route is
unpopulated, so refresh uses the MLB StatsAPI source already present in the
repo: `/api/v1.1/game/{official_game_id}/feed/live`.

There is one feed request per eligible game per capture, cached in `feeds.json`;
at most one retry is allowed for transient errors. Games past T-10 are skipped.
The slate is bounded at 40 games and duplicate IDs are rejected. There are no
per-player loops or new historical downloads. Nonfatal feed failures record
HTTP status and redacted body shape and use the current schedule/team prior.

A feed must match official game ID, both team IDs, start time, Preview state,
and a fresh pre-T-10 retrieval time. Confirmation requires nine unique positive
player IDs, each player's matching ID and original batting slot, and
`isSubstitute=false`. These are the evidence rules already used in
`hello_world/mlb_statsapi_team_context.py`. Partial, malformed, live, stale,
future, or mismatched evidence produces a projected lineup. Hash corruption
still fails closed. The later valid feed's probable-pitcher field is
authoritative, including a cleared pitcher; schedule fallback cannot resurrect
that cleared pitcher. The source only establishes **probable** starters, so
KS1 does not invent a confirmed-starter flag.

## Row status and model behavior

| Column | Meaning |
|---|---|
| `status`, `prediction_status` | `projected_missing_starter` when either pitcher is absent; otherwise `confirmed_lineups` when both orders are verified, or `projected` |
| `lineup_status` | `confirmed` only when both sides have verified orders; `projected` otherwise |
| `home_lineup_status`, `away_lineup_status` | Per-side confirmed/projected evidence |
| `home_lineup_ids`, `away_lineup_ids` | Ordered JSON array of official player IDs, or null |
| `home_offense_source`, `away_offense_source` | `team_prior`: the accepted, shrunk 10/30/75-day team offense inputs |
| `starter_feature_source` | `team_starter_prior`: the accepted team-starter process inputs |
| `lineup_source_status` | Verified pregame feed, unavailable feed, unverified feed, or conservative legacy status |
| `starter_source` | Verified MLB feed probable pitchers, otherwise the existing schedule probable pitchers |
| `as_of` | Cutoff of the last material row build, preserved when inputs are unchanged |
| `input_fingerprint` | Semantic row inputs, accepted model versions, feature-contract version, and all features consumed by either model |

The accepted models were trained on team offense and team-starter features.
Confirmed orders are recorded, but they do not silently replace those feature
definitions with individual-player estimates. An identity-only scratch
rebuilds its game and updates its identity/status/cutoff; its probability and
expected runs can remain the same. This phase does not claim an accuracy gain.

Quote values and market availability participate in the fingerprint, including
totals and spreads. Expired quotes are a material change. Retrieval timestamps
and history refresh timestamps alone are excluded. The run report records the
latest poll time, feed receipts, changed/unchanged IDs, change reasons, removed
IDs, and frozen rows. New `lineup_cache.json` accompanies the existing odds
cache and crosswalk in the same date prefix. Predictions contain the resolved
identities/statuses and market values and do not require an atomic multi-file
read. Each S3 object retains the existing conditional-write/readback checks.

Existing Phase 4 upcoming rows upgrade once to the new fingerprint/status
contract. Frozen Phase 4 rows only receive conservative status defaults;
their predictions and original timestamps remain intact. Every resulting row
has `status` and `lineup_status`. Subsequent unchanged reruns are byte-identical.
The existing T-10 cutoff remains; late changes after that cutoff do not alter
frozen predictions. The hourly schedule can miss a late announcement.

## Files changed and local execution

- `ks1/refresh.py`: verified orders, starter fallback, semantic fingerprints.
- `ks1/live_inputs.py`: bounded optional feed capture and same-date previous file.
- `ks1/daily.py`: status columns, selective inference/reuse, cache and reporting.
- `ks1/verify_refresh.py`: offline proof using retained inputs and accepted models.
- `tests/ks1_phase5/test_refresh.py`, `tests/ks1_phase4/test_daily.py`: regression and date-write checks.
- Existing ingestion workflow: refresh step label and Phase 5 PR test coverage.
- This document and `docs/ks1/phase5-proof.json`: commands and recorded results.

Python 3.11+. With the existing read credentials available:

```bash
python -m pip install -r ks1/poisson-requirements.txt 'pytest>=8,<9' 'PyYAML>=6,<7'
python -m ks1.live_inputs --date 2026-09-10 --output /tmp/ks1-refresh-inputs
python -m ks1.daily --inputs /tmp/ks1-refresh-inputs --output /tmp/ks1-refresh
python -m pytest -q tests/ks1 tests/ks1_phase4 tests/ks1_phase5
```

`live_inputs` reads the previous date snapshot from S3 automatically. Local
`daily` never publishes without `--publish`, and publication is only permitted
on main in the existing ingestion workflow. For a fully offline rerun proof,
using an early retained Phase 4/5 input capture and its pinned `model.txt`:

```bash
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 python -m ks1.verify_refresh \
  --inputs /tmp/ks1-live-inputs --output /tmp/ks1-phase5-proof
```

The proof explicitly marks later captures and the scratch as synthetic.
Publication rejects those captures. Only its baseline output is an unmodified
source replay. The verifier performs no network calls, training, or AWS writes.

## Verification result

47 local tests passed, including confirmed-order changes, one-game scratches,
missing and malformed lineups, cleared pitchers, stale captures, legacy status
migration, frozen rows, conditional same-date overwrite, and another date left
untouched. CI uses a classifier spy to count scored rows and the actual
accepted Poisson weights. The separate local proof uses **both real accepted
model artifacts**, with no mocks, on the five-game September 10 retained slate
captured at `2026-09-10T03:20:07.587886+00:00`:

| Scenario | Scored | Unchanged | Result |
|---|---:|---:|---|
| Initial retained-source replay | 5 | 0 | All five have status and projected lineup fallback |
| Later poll, same semantic inputs | 0 | 5 | All rows and Parquet bytes identical |
| Controlled starter replacement for game 824872 | 1 | 4 | Only that game changes |
| Repeat of the replacement input | 0 | 5 | All rows and Parquet bytes identical |

Baseline Parquet SHA-256:
`0e77dbb2572ca4f2ae2655b672444a205c6d8b65b61a359de3cb3528684af658`.
Controlled scratch Parquet SHA-256:
`6253871138b0cce07ff5eaf0129980484d61a440e7d4948321c19ad4ce0c55ae`.

A separate live feed probe for official game 824872 returned HTTP 200 at
`2026-09-10T03:47:16.162963+00:00`, verified both probable pitchers, and contained
empty batting orders. The parser returned projected/team-prior statuses. That
later observation was not inserted into the earlier retained-source replay.
Confirmed-order and scratch scenarios are controlled tests, not claims of
announced live lineups or real scratches. No AWS writes or deployment occurred.
