# Statcast recovery and development-only selection

The qualification artifact from [main run 34892538641](https://github.com/KirtKurt/parlay-platform/actions/runs/34892538641)
contains 4,732 table rows but no non-null values in any of the eight matchup
columns. Its validator rejected 372 historical dates, admitting only eight
nonempty daily objects. All three recipes lost to the incumbent on the final
300 games. The full recipe also failed substantive matchup usage.

The existing source collector only retries a cached day when its game set
changes. A cached response with the correct IDs but incomplete physical-pitch,
PA, or outcome evidence remains stuck. This repair adds a training-specific
recovery path; it does not reinterpret incomplete fields as observations.

## Recovery

- Run only inside the existing authorized main training workflow. No PR
  provider calls or new credential path. At most 64 daily requests and 1,200
  seconds per run; access/rate-limit responses stop further provider requests.
- Retry never-attempted dates first, newest first within that group, then older
  attempts. Skip a same-game-set retry already attempted on the current UTC day.
- Retain successful responses under content-addressed recovery objects. The
  original archive is untouched. A conditional date pointer records the exact
  version and checksum after verified readback.
- Reapply the existing exact physical-pitch, PA identity/count, outcome-field,
  date, and official-game-set rules on every read. A pointer is not admission.
- Failed retries preserve a previously verified pointer for an unchanged game
  set. A transient read error cannot strand the retained source; changed game
  sets still require new verification.
- Emit specific rejection reasons. Missing provider fields are never filled,
  counts are never given a tolerance, and incomplete windows remain unknown.

The workspace's two direct Savant probes returned HTTP 403. Real recovery must
be measured in the hosted workflow, and may remain blocked if the provider
continues returning incomplete fields. Tests demonstrating a complete response
are synthetic regression evidence, not claims of recovered production dates.

The hosted raw-only recovery subsequently fetched 64 dates successfully but
recovered none because their PA outcome fields were still incomplete. See
[official outcome reconciliation](official-outcome-reconciliation-20260914.md)
for the independently evidenced follow-up. It preserves raw fields and derives
only deterministic accounting in a separately verified payload.

## Development before qualification

`ks1/qualification_holdout_20260914.json` reserves the original ordered 300 game
IDs, labels, feature cutoffs, completion timestamps, and incumbent hash from
run 34892538641. Changed evidence fails explicitly. New completed games cannot
slide the cohort. Training labels must precede its earliest feature cutoff.
Historical reconstructed feeds remain retrospective, not prospective receipts.

Four prespecified LightGBM configurations are tried for each of the three
recipes on a purged 300-game development tail. The search uses 4,091 fit games
from 4,395 source-qualified training games. Each recipe may replace its original
configuration only with lower development Brier and no worse development log
loss. All feature-admission rules, including non-null coverage thresholds, are
recomputed on the development fit partition. Invalid training labels fail before
fitting. Final holdout rows never enter the selector. The baseline training module,
native probability model format, and serving references are unchanged.

On the original incomplete input table, all recipes selected the shallow
configuration (three leaves, 100 estimators, L2 30):

| Recipe | Original development Brier | Selected development Brier | Selected log loss |
| --- | ---: | ---: | ---: |
| Starter | 0.252264452 | 0.245271922 | 0.683727018 |
| Starter + batters | 0.251145117 | 0.245969291 | 0.685088522 |
| Starter + batters + bullpen | 0.250266726 | 0.245973118 | 0.685080854 |

The selector chose starter. This is development evidence only, not a winning
full candidate or qualification result. Search is deferred from qualification
when matchup values are absent, the full recipe is not selected, or its fitted
development trees do not use substantive batter/matchup/bullpen values. The
report then says `accepted=false`, `qualification_run=false` and records why.
After those conditions pass, the existing final Brier, log-loss, source and
feature-usage gates still decide acceptance. No automatic serving promotion.

Reproduce the development-only experiment after downloading the source run's
artifact to `qualification/`:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=2 python -m ks1.development \
  --input qualification/input_table.parquet \
  --proof qualification/input_proof.json --output development-result
```

The input is hash-checked before training. Full selection evidence is written
to `development_selection.json`. The trusted main trainer includes this file,
the frozen cohort and recovery diagnostics in its ordinary Actions/S3 artifact.
The existing weekly workflow will retain this fixed experimental cohort until
an explicit new-cohort change; it cannot silently qualify on different games.

T-10 locking, the 2 a.m. audit, model refs, live serving code, prediction
publication and official ledgers are outside this patch.

Parameter meanings: [LightGBM parameters](https://lightgbm.readthedocs.io/en/stable/Parameters.html).
Raw-field contract: [Savant CSV documentation](https://baseballsavant.mlb.com/csv-docs).
