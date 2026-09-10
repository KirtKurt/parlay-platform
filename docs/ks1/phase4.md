# KS1 Phase 4: daily predictions in the existing job

Local prediction ran successfully before this PR was opened. This PR is stacked
on Phase 3 (#699), which is stacked on Phase 2 (#698). None is merged by this
task. KS1 does not replace or modify R8 authority.

## Existing execution path

The existing `.github/workflows/mlb-research-ingestion.yml` remains the sole
trigger: hourly at minute 23, existing main pushes, or manual dispatch. The
same `ingest` job prepares research inputs, captures KS1's live data, scores,
and publishes to the existing AWS artifact bucket. No new schedule, Lambda,
SAM resource, infrastructure stack, or deployment workflow is added. A PR-only
test job has no provider or AWS credentials. Publication requires main in this
exact workflow; PRs and local runs cannot publish.

Actual credential names: `BBS_API_KEY`, `ODDS_API_KEY`, `AWS_ACCESS_KEY_ID`, and
`AWS_SECRET_ACCESS_KEY`. They already exist in GitHub's deployment configuration.
The artifact bucket is resolved through the existing `MLBMLTrainingFunction`
environment variable `MLB_ML_ARTIFACTS_BUCKET`; no bucket is invented or created.

## Live inputs and model inputs

- BBS `GET /v1/matches?sport=baseball&league=mlb&date=...&limit=200` supplies
  provider game/team IDs, match names, status, and start times. Dates are the
  distinct UTC dates spanned by the official Eastern slate. A known legacy
  score-envelope response falls back to `/v1/stored/matches`; score records
  are never accepted as game identities. Truncated or unmatched slates fail.
- The existing MLB StatsAPI schedule source supplies official IDs and observed
  probable pitchers, using one bulk `hydrate=probablePitcher,venue(location)`
  call. Current BBS match rows lack pitcher fields, and its documented stored
  lineup feed is not populated. Missing pitchers are null and flagged; no
  guessed identities or per-player API loops are used.
- The Odds API is requested once per capture with `regions=us`,
  `markets=h2h,spreads,totals`, `oddsFormat=american`. Transient transport/5xx
  failures allow one retry; 401/403/429 stop with redacted status/body shape.
  There are no historical-odds requests or BBS odds calls.
- Existing compact/full S3 boxes are reused. No archive is downloaded from
  any provider. `Features.at` retains Phase 1's shrinkage and past-completion
  rules. An optional game date makes next-day early forecasts use the correct
  rest/window date while still rejecting results completed after capture.
  Default Phase 1 behavior is unchanged. Known missing prior boxes cannot be
  interpreted as zero bullpen workload.
- Exact team aliases learned from official IDs plus unique start-time matching
  bind BBS/Odds to games, within 90 seconds. Ambiguous doubleheaders are not
  guessed. A BBS-to-MLB crosswalk with confidence/method is saved per date.

`p_home` uses the accepted LightGBM artifact, version-bound to its existing S3
object in `mlb/experiments/ks1-phase2/`. Run rates use the accepted Phase 3 JSON
model, copied unchanged from run 34432200300 into `ks1/poisson_model.json`.
Both hashes are pinned in `ks1/model_refs.json`. No model is retrained.
Probable identities are recorded, but these frozen models learned team-starter
process features because individual-starter training coverage was absent.

## Output contract and publication

`mlb/ks1/predictions-v1/date=YYYY-MM-DD/predictions.parquet` contains:

| Columns | Meaning/source |
|---|---|
| date, game_id, home_team, away_team, home_id, away_id, commence_time | Eastern date and official game/team identities |
| bbs_game_id, bbs_home_id, bbs_away_id | Actual BBS list-endpoint IDs, verified against the official slate |
| home_starter_id, away_starter_id, home_starter_name, away_starter_name | Probable pitchers from the current official bulk schedule; nullable |
| home_starter_status, away_starter_status, starter_source | Probable/missing and explicit provenance |
| p_home | Frozen LightGBM home-win probability |
| lambda_home, lambda_away, proj_total | Frozen Poisson expected home/away runs and their sum |
| p_home_poisson | Poisson home-win probability with the accepted 50/50 tie allocation |
| market_home_prob | Mean paired-book, vig-removed probability; quotes must be no older than 15 minutes and not future dated |
| market_total, market_spread | Median paired total and home handicap across valid books |
| edge_home, edge_total | p_home minus market_home_prob (probability units); proj_total minus market_total (runs); null when market missing |
| odds_event_id, odds_match_confidence, market_status | Unique market identity and coverage |
| model_version, as_of, input_fingerprint | Both model hashes, source-capture UTC cutoff, input fingerprint |
| lineup_status, prediction_status, history_source_as_of, history_status, environment_status | Projected lineup; missing-starter flag; retained-history freshness/coverage; unavailable environment inputs |

The date is dictionary-encoded so the complete file is also readable with
automatic Hive partition discovery. It remains the string YYYY-MM-DD.

Every new prediction is captured before T-10. Repeated captures of the date
replace upcoming-game rows; previous valid rows past their cutoff are retained
with their original as_of and model version. A date's previous Parquet is read
before scoring, and conditional S3 writes reject concurrent/stale replacement.
Rerunning the identical input produces identical bytes and no repeated S3 writes.
Writes are restricted to that date's KS1 prefix. No date prefix is deleted.

The same prefix stores `odds_cache.parquet` (raw event JSON plus receipt time)
and `crosswalk.json`. Predictions carry their market values so they do not
depend on another file being read atomically. Parquet writes are atomic per
object and hash-readback verified. Local output also includes CSV and report JSON.

## Local execution and proof

Python 3.11+. In an environment with the existing read credentials:

```bash
python -m pip install -r ks1/poisson-requirements.txt 'pytest>=8,<9' 'PyYAML>=6,<7'
python -m ks1.live_inputs --date 2026-09-10 --output /tmp/ks1-live-inputs
python -m ks1.daily --inputs /tmp/ks1-live-inputs --output /tmp/ks1-daily
python -m pytest -q tests/ks1 tests/ks1_phase4
```

This workspace had no credentials. A read-only branch run of the existing
workflow captured inputs using GitHub secrets, and its hash-verified data
artifact was downloaded for local prediction. No secret was downloaded.
[Capture run 34432848402](https://github.com/KirtKurt/parlay-platform/actions/runs/34432848402):
both provider HTTP statuses were 200, one Odds request cost 3 quota units,
2,185 retained current-season game boxes were reused, and AWS writes were zero.

The local September 10 run used captured as_of `2026-09-10T03:20:07.587886+00:00`.
It produced 5/5 scheduled games, with fresh moneyline/total coverage on all five
and both probable pitchers on four. Chicago White Sox's starter was absent.
The September 9 late live game in BBS's UTC response was excluded.
Twenty-five local checks passed. Two complete local runs were byte-identical,
including Parquet SHA-256
`626b16a570aeee7a7201bcc67edc051542fd7b85b92bbbe8d9ce33ddf7f95172`.

Saved-model performance remains the Phase 2/3 result; daily integration does
not establish improved accuracy. No lineup refresh, simulation, Phase 5 work,
PR merge, or deployment is included. Stop after the local file and PR.

Sources: [BBS OpenAPI](https://bigballsdata.com/openapi.json),
[The Odds API v4](https://the-odds-api.com/liveapi/guides/v4/), and the repo's
`mlb_research/mlb_research_sources_v1.py` / `mlb_auto_llm/handler.py` contracts.
