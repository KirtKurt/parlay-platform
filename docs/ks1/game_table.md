# KS1 Phase 1 game table

KS1 has its own package and S3 output namespace. Phase 1 materializes existing
data; it does not train, score predictions, change R8, or add a scheduler.

## Run and verify locally

Python 3.11 or newer, with read access to the existing source buckets and
CloudFormation/Lambda configuration:

```bash
python -m venv .venv-ks1
.venv-ks1/bin/pip install -r ks1/requirements.txt 'pytest>=8,<9'
.venv-ks1/bin/python -m pytest -q tests/ks1
.venv-ks1/bin/python -m ks1.build --output /tmp/ks1-phase1
.venv-ks1/bin/python -m ks1.build --date 2026-09-09 --output /tmp/ks1-one-date
```

The builder produces `game_table.parquet`, `report.json`, `sample_20.csv`,
`column_sources.csv`, and `crosswalk.json`. The report includes season counts,
both-starter and final-score percentages, missingness by column, exclusions,
and source object version IDs and byte hashes. The 20-row sample is evenly
spaced through the chronological table. Every parquet column has a named
entry, type, role, source and description in `column_sources.csv`.

The job rebuilds one date independently and compares it with the corresponding
full-build rows. Tests cover target/future/suspended-game exclusion, shrinkage,
label isolation, conflicting scores, original-starter timing and identity,
market timing and vig, and atomic date-scoped publication.

## Sources and joins

| Columns | Existing source | Rule |
|---|---|---|
| `game_id`, `date`, `season`, teams/IDs, start | Reconstructed game contract; official schedule; compact/full boxes | Official MLB `gamePk` and team IDs; one row per game |
| `home_score`, `away_score`, `home_win` | Historical `mlb/historical-daily-v1/official-finals/{date}.json`, research dataset, full boxes, final schedule | Final status required; conflicting sources stop the build; label-only |
| Pregame starter IDs/names/status | Original `research-v1/snapshots/` playerWindows, or schedule observed while Preview before cutoff | Final-box identities never fill these fields |
| Actual starter IDs/names | Existing final full player boxes, `stats.pitching.gamesStarted == 1` | Audit labels only; never model inputs |
| `*_offense_ops/iso/pa/games_{10,30,75}d` | Earlier completed compact and full team batting boxes | Calendar windows, shrunk OPS/ISO; counts remain observed |
| `*_team_starter_*_{10,30,75}d` | Earlier completed compact starter groups / full starters | Team starter-group K-BB%, WHIP when hits available; distinct from individual starter |
| `*_starter_*_{10,30,75}d` | Earlier completed full player pitching boxes | Individual pitcher only when pregame starter ID is known; otherwise null |
| `*_bullpen_pitches/outs_{1,3,5}d` | Earlier completed compact relief usage / full relief boxes | Observed workload; missing counts remain missing |
| Lineup IDs/status | Original pregame playerWindows | Confirmed only with nine distinct observed players; otherwise projected; Phase 1 offense uses team prior |
| `park`, `park_id` | Stored official schedule / full box venue | Identity only; no invented park coefficient |
| `temp`, `*_travel_km` | Original conditions snapshot | Archived forecast in Fahrenheit and recorded travel in km |
| Park factors, wind | Not available in admitted sources | Null; environment status records forecast availability |
| `market_home_prob` | Reconstructed/snapshot no-vig probability, retained odds-v8 books | Pregame timestamp; paired-book vig removal and multi-book mean |
| `market_total`, `market_spread` | Retained `mlb/odds-v8-shadow/` objects | Exact observed names + start within 90 seconds, unique event; paired totals/spreads and quote age ≤15 minutes |
| `as_of_timestamp`, evidence/source fields | Original capture time or archived reconstructed cutoff | At or before T-10; separate from final-label observation |

Targets are the union of the admitted reconstructed cohort and the retained
recent official schedule/original snapshots. Older compact games provide
warmup history; they are not silently added as new targets. Regular season and
postseason games are included. Unsupported schedule states and missing official
team identities are reported as exclusions. A future cutoff does not make a
late-fetched source an original observation.

Crosswalks are built once from observed official team/player IDs and aliases.
When a target box is absent, an exact archived team name is resolved only if the
observed crosswalk contains exactly one official team ID for that name. Each
side records its identity method and deterministic match confidence; ambiguous
names remain exclusions.
No BBS IDs are invented. Market name matching is exact and uniquely time-bound;
ambiguous mappings remain null. There is no fuzzy matching in this release.

## Time and missingness contract

Prior games must have `completedAtUtc < as_of_timestamp` and a strictly earlier
Eastern calendar date in the same season. This conservatively excludes
same-day doubleheader results. A suspended game completed later is excluded.
Rates shrink toward an expanding, strictly prior current-season league prior:
100 PA/AB for offense, 100 batters faced for K-BB%, and 75 outs for WHIP.
There is no future-derived cold-start prior. Raw ERA and raw W-L are absent.

Small samples remain visible through count columns. Null means unavailable;
zero is used only for observed counts/no appearances with available history.
The compact historical boxes contain starter groups but no individual starter
IDs or hits allowed, so individual identity and historical WHIP coverage are
limited. The report separates **both pregame starters** from **both actual
starters in final boxes**. Neither coverage implies prospective qualification.

The retained Statcast store was inventoried but is not reingested here. Its
contact-only `estimated_woba_using_speedangle` is not represented as full xwOBA;
barrel%, xERA, SIERA and xFIP are not fabricated from absent fields. Additional
process/environ­ment features remain a later-phase extension.

No BBS request is made by this existing-data build. Stored final scores are
joined before considering gaps. Current [BBS OpenAPI documentation](https://bigballsdata.com/openapi.json)
states that stored lineups are not yet ingested. Fetching a historical box now
also cannot establish what starter was known at a past pregame cutoff. Such
gaps stay explicit. The canonical repo credential names are `BBS_API_KEY` and
`BBS_API_SECRET_ARN`; their values are not needed or exposed by this job.

## Deployment and date idempotence

The existing `.github/workflows/ks1-phase1.yml` performs read-only builds on
same-repository PRs. Merging KS1 code to main publishes through that workflow;
manual dispatch supports an optional date. No cron or AWS infrastructure is
added. Publication is restricted in code to this repository's main push/manual
GitHub job; local builds cannot publish using `--publish`.

The bucket is resolved from deployed `MLBMLTrainingFunction` configuration via
`MLB_ML_ARTIFACTS_BUCKET`. The deliberately new output namespace is:

```text
mlb/ks1/game-table-v1/date=YYYY-MM-DD/
  games-<byte-sha256>.parquet
  sources-<byte-sha256>.json.gz
  manifest.json
```

Each date manifest points to a complete version-bound parquet and a compressed
source-receipt list. Files are written before the manifest pointer. Publication
reads parquet back and compares its hash and table values. Identical rows are
a no-op; changed rows update only their date prefix. Other dates and all R8
prefixes remain untouched. Consumers should resolve each date's `manifest.json`
instead of globbing all historical parquet versions. A full build intentionally
iterates every selected date; `--date` publishes only that date. Output artifacts
and a `deployment.json` readback receipt are retained in the GitHub run.

Phase 1 stops after this table deployment. No model artifact or prediction
service is claimed by this release.
