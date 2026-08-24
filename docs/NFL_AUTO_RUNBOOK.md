# NFL Auto — Autonomous Historical Training and 2026 Regular-Season Runbook

## Operating decision

The stack is **historical-only through September 8, 2026**. The live collector is date-gated until **2026-09-09T04:00:00Z**, which is midnight EDT on the date of the first 2026 regular-season game. The first public prediction can only be frozen from an eligible regular-season board at least ten minutes before kickoff.

The preseason sport key is never queried. BBD rows with `type=PRE` are rejected. A live invocation before the activation boundary returns:

```json
{
  "ok": true,
  "status": "HISTORICAL_ONLY",
  "preseason_predictions": 0
}
```

## Isolation contract

The deployment creates the independent CloudFormation stack `parlay-platform-nfl-auto`. Its runtime role can access only:

- Dedicated `Nfl*` DynamoDB tables.
- Dedicated raw and model-artifact S3 buckets.
- Dedicated BBD and Odds API secrets.
- The configured Bedrock inference profile.
- Its own asynchronous failure queue.

It has no IAM permissions to mutate MLB, tennis, soccer, or legacy resources. The deployment workflow also rejects cross-sport resource names in the NFL SAM template.

## Data authority

| Responsibility | Authority | Policy |
|---|---|---|
| Schedule, finals, venue/rest context | BBD NFL | 2020–2025 `REG` and `POST`; 2026 `REG` only after activation |
| Play-by-play, EPA/WP-derived team form | BBD NFL | Point-in-time rolling features; current game is appended only after its pregame vector is created |
| Moneyline, spread, total | The Odds API | US-region bookmaker consensus at T-24h, T-60m, and immutable T-10 |
| Hyperparameter proposals | Amazon Bedrock | JSON-only allowlisted trials; no data deletion, provider changes, gate changes, or publication authority |
| Model promotion | Deterministic evaluator | Out-of-time log loss, calibration, and paired bootstrap skill versus the same-time market |

Every training row must contain both a BBD provenance digest and an Odds API provenance digest. Missing provider evidence excludes the row; it is not imputed or reconstructed.

## Historical corpus

The autonomous state machine processes these phases in order:

1. `BBD_GAMES` — discover 2020–2025 regular-season and postseason games.
2. `BBD_PLAYS` — retrieve play-by-play and aggregate pregame-safe team statistics.
3. `ODDS_SNAPSHOTS` — retrieve featured-market snapshots at T-24h, T-60m, and T-10.
4. `MATERIALIZE` — join providers, create immutable target rows, and record explicit exclusion reasons.
5. `READY` — train, evaluate, and promote eligible historical champions.

The cursor is stored in DynamoDB after every successful unit of work. Lambda reserved concurrency and a DynamoDB lease prevent duplicate workers. A failed or throttled provider call does not reset the corpus.

### Shared Odds API protection

Historical requests stop when remaining credits reach the configured reserve:

```text
reserve = total_plan_credits × reserve_percent + race_buffer
```

Defaults:

- `SharedQuotaReservePercent=20`
- `QuotaRaceBufferCredits=2000`

This preserves a portion of the shared all-sports plan for existing MLB, tennis, soccer, and live workloads. Live NFL collection has priority once the regular season starts.

## Feature set

The first model schema contains 23 frozen features:

- Home/away and differential offensive EPA per play.
- Defensive EPA allowed and home defensive edge.
- Pass EPA and rush EPA differentials.
- Success-rate and explosive-play differentials.
- Turnover-rate edge.
- Early-down pass tendency and third-down success differentials.
- Rest-day differential and prior-games availability.
- Target line, bookmaker count, and bookmaker probability dispersion.
- T-24h→T-10 and T-60m→T-10 probability movement.
- T-24h→T-10 and T-60m→T-10 line movement.

Rolling team statistics are produced before the current game is appended, so the current result and play-by-play cannot leak into its feature vector.

## Prediction targets

Three independent residual models are trained:

1. `moneyline_home_win`
2. `spread_home_cover`
3. `total_over`

The model learns a correction to the de-vigged bookmaker consensus rather than pretending the market does not exist. Ties and exact spread/total pushes are excluded from the affected target only.

## Training and audit

### Initial historical promotion

- Training: seasons 2020–2023.
- Validation: season 2024.
- Untouched audit: season 2025.

A candidate is rejected unless it:

- Meets the minimum row counts.
- Beats the market on validation log loss.
- Beats the market on 2025 audit log loss.
- Has a positive 95% paired-bootstrap lower bound for audit skill versus the market.
- Meets the audit calibration ceiling.
- Does not regress relative to an existing champion on the same audit.

### Autonomous 2026 learning

Settled 2026 games are materialized from the original immutable T-10 prediction evidence. Before enough prospective evidence exists, the 2025 audit remains untouched. After at least 144 eligible live rows per target:

- Earlier historical rows and the oldest 2026 rows become training evidence.
- The next 48 live rows become validation.
- The newest 48 live rows remain an untouched prospective audit.

The windows move forward chronologically as new games settle. This lets the system learn from 2026 without evaluating a challenger on rows it trained on.

## Bedrock analyst boundary

Bedrock receives only aggregate training/validation summaries and the fixed feature schema. It may propose values for:

- `learning_rate`
- `l2`
- `epochs`

The response is parsed as JSON and range-validated. Invalid proposals are discarded. Bedrock cannot:

- See reserved audit outcomes during trial selection.
- Add or remove provider evidence.
- Change the T-10 rule.
- Relax promotion gates.
- Promote a model.
- Publish a prediction.

If Bedrock is throttled or unavailable, the deterministic baseline trial set continues and the LLM status is recorded as degraded. Runtime health and model-promotion status remain separate.

## 2026 regular-season transition

The live EventBridge schedule runs every five minutes but makes no live provider calls before the activation timestamp. Beginning September 9:

1. BBD’s 2026 `REG` schedule is synchronized.
2. The Odds API live board is captured.
3. Missing T-24h/T-60m points are backfilled from historical snapshots, one per invocation.
4. A T-10 board is accepted only when captured between 16 and 10 minutes before kickoff.
5. The same feature schema used in training is frozen.
6. Each target writes one immutable prediction using `attribute_not_exists(PK)`.
7. After a game settles and BBD play-by-play is available, the game is added to the prospective learning corpus.

If there is no promoted historical champion, insufficient bookmakers, missing dual-provider provenance, or a late T-10 snapshot, publication is blocked rather than fabricated.

## Required GitHub secrets

The included deployment workflow expects:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_REGION`
- `ODDS_API_KEY`
- `BBS_API_KEY`

`BBS_API_KEY` is the existing repository convention for the BBD credential.

## Deploy

1. Apply `nfl-auto.patch` at the repository root, or copy the bundle contents into the repository.
2. Commit the new NFL-only files.
3. Run **Deploy Isolated NFL Auto** from GitHub Actions.
4. Keep the default activation timestamp unless the official opening date changes.
5. Review the workflow summary for credential proof, preseason-gate proof, first cursor advancement, and the status URL.

The workflow runs source compilation, all unit tests, static isolation verification, SAM linting, provider credential smoke checks, a pre-activation live invocation, and a first historical cursor invocation before reporting success.

## Status interpretation

| Status | Meaning |
|---|---|
| `HISTORICAL_ONLY` | Correct before September 9; zero live board calls and zero preseason predictions |
| `BBD_GAMES` | Historical schedule/finals discovery is advancing |
| `BBD_PLAYS` | Historical play-by-play aggregation is advancing |
| `ODDS_SNAPSHOTS` | T-24h/T-60m/T-10 historical lines are advancing |
| `DEFERRED_BBD_RATE_LIMIT` | BBD daily/rate allowance is exhausted; cursor is preserved |
| `DEFERRED_SHARED_QUOTA_RESERVE` | NFL backfill reached the reserved Odds API floor; other sports are protected |
| `MATERIALIZE` | Dual-provider rows are being frozen |
| `TRAINING` | Challenger search/evaluation is running |
| `PROMOTED_HISTORICAL_CHAMPION` | Target passed all historical gates |
| `REJECTED_BY_GATE` | Runtime can be healthy while the model correctly remains unpromoted |
| `REGULAR_SEASON_LIVE` | Date gate is open; only 2026 `REG` events are eligible |

## Local verification performed for this bundle

```text
python -m py_compile nfl_auto/*.py
python scripts/verify_nfl_auto_bundle.py
python -m pytest -q tests/nfl_auto
```

Result at bundle creation: **14 tests passed**.

## Deliberate first-release boundary

This release starts with moneyline, full-game spread, and full-game total because those markets have the complete 2020–2025 overlap needed for the strongest historical audit. NFL player props and quarter/half markets have a shorter historical window and require separate target-specific settlement logic. They are not silently mixed into the core training corpus.
