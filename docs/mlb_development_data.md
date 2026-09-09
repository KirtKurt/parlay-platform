# MLB development data and admission

`MLB historical data and daily admission` prepares historical data daily at
10:37 UTC and on manual dispatch. It does not invoke a training mode or change
the deployed model. EventBridge remains the automatic production learning owner.

The job verifies the existing archived odds/results datasets, then combines
them with statistics from prior completed regular-season games. It writes a
separate versioned, checksum-verified S3 dataset under
`mlb/development-data/reconstructed-v1/`. The exact S3 version and checksum are
in `runtime_reports/mlb_data_admission_latest.json`. Source games are cached in
that same isolated namespace so subsequent preparation runs can reuse them.

Each row contains a market probability, archived odds movement, prior 14-day
team batting OPS, prior 14-day starting-pitcher group statistics, and prior
1/3/5-day relief workload. These pitcher statistics describe previous starters
for the team; they are not statistics for the target game's probable starter.
Home-minus-away features are also available directly in the `features` object.
The outcome is stored separately in `label` and is joined after feature hashing.

Current-game starter identities and pregame batting orders are explicitly
missing where no archived pregame receipt exists. A historical final boxscore
cannot establish when those identities or lineups became known. Prior source
games must have completed before the target day's midnight in Eastern time;
same-day games and later suspended-game completions cannot contribute. Later
official statistical corrections may be present, so this dataset is labeled
reconstructed development data, never original live or prospective evidence.

Researchers can use these rows for chronological development experiments.
Use whole-slate splits, fit imputation/scaling on the training partition only,
compare predictions against each row's same-time market probability, and keep
these reconstructed features distinct from the v2 successor's live features.
No development result grants production authority. A successor must still
freeze its protocol and model, collect a new prospective test, and pass review.

The daily admission reports separately inspect original canonical live locks
and final labels. The summary reports collection, locks, settlement, source
coverage, and admissible settled rows per date. The companion
`mlb_data_admission_rows_latest.json` records every inspected game's rejection
reason and snapshot validation errors. Original evidence is never rewritten.
The existing progress pulse displays the daily summary, including its age;
reports older than 30 hours are marked stale.
