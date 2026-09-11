# Autonomous MLB feature discovery — bounded research increment

This extends the existing AWS research owner; it is not a second trainer,
new scheduler, new prediction authority, or unrestricted LLM coding service.
KS1 serving forecasts, immutable locks, calibration, other sports, and the
existing promotion-review contract remain unchanged.

## What the research worker can now do without conversational prompts

For each eligible development experiment, it evaluates its five existing
model configurations plus one `adaptive_linear` challenger. Inside each
chronological training fold, the challenger chooses eligible raw signals,
generates products, differences, squares, and absolute-value transforms of
training-normalized inputs, retires inputs from that challenger when coverage
or the bounded ranking excludes them, and fits a regularized market-offset
classifier. It records its generated feature plan and emits portable Python
source from trusted templates for reproduction and inspection.

The same input cohort and implementation produce the same experiment. New
eligible training games change the deterministic exploration seed, allowing
different combinations to be explored. No wall-clock randomness or held-out
outcomes guide candidate generation. Identical failed experiments remain
cached; new source/data/protocol identities are separate immutable records.
The existing owner controls when research is due and when a prospective test
must finish before another experiment is permitted.

## Computation and execution bounds

Discovery uses at most 1,200 training games and 512 input names. It retains at
most 24 raw features, explores an eight-feature interaction pool, considers
at most 96 formulas, and retains at most four nonredundant generated features.
There are three expanding whole-slate validation folds and at most one final
adaptive refit. Missing input coverage below 80 percent prevents selection;
normalization is fitted exclusively on that training fold and clipped.

Inference interprets a checked, checksum-bound mathematical recipe. It does
not execute the generated source, call eval/exec, install packages, access
credentials, make network requests, or permit arbitrary code edits. Generated
Python must exactly match the recipe and retained source hash. Existing raw
snapshot inputs are not rewritten. `features` remains the raw dependency list
used by the worker's coverage checks; `matrixFeatures` separately records the
classifier's actual engineered matrix columns.

The research manifest automatically hashes newly introduced `mlb_*.py`
modules, while still requiring the known source files. It remains unaffected
by unrelated sport, report, or deployment-ID changes.

## Selection and validation

Feature discovery, normalization, ranking, and pruning run only on the
training portion of each expanding fold. Validation outcomes never enter
feature generation for that fold. To be eligible for the final held-out
comparison, adaptive discovery must improve walk-forward Brier score by at
least 0.001 and reduce log loss versus the best existing model configuration.
An improvement in training correlation is never reported as performance proof.

Only the selected configuration is refit and evaluated on the final 20 percent
whole-slate holdout. Historical acceptance retains all existing market,
calibration, sample-size, and coverage gates. It remains historical screening,
not production qualification or evidence of future profitability.

A historically accepted frozen candidate still needs 100 genuinely new,
immutable pregame predictions and its established prospective test. Active
future tests are not restarted because a new implementation is deployed;
sealed tests are not reopened. Failed tests still require 100 new games before
another freeze. The existing first-activation review is not bypassed. No
confidence or probability is increased merely to produce a stronger-looking
pick. No supported market is added by this patch.

## Evidence and limitations

Automated tests cover deterministic training-only programs, coverage bounds,
reserved label exclusion, missing/extreme inputs, checksum/source corruption,
standalone Python replay, serialized adaptive inference, legacy frozen-model
inference, chronological-fold isolation, and stricter challenger selection.
Synthetic test success demonstrates software behavior, not MLB accuracy.

Once the deployed owner next runs an eligible experiment, `training.json`
and the immutable experiment record provide the real-data result. Generated
formulas can be inspected in `featureDiscoveryFolds`; an adaptive final model
also retains its complete `featureProgram` and `generatedFeaturePython`.
Record candidate rejection as evidence rather than relaxing a gate to obtain
an accepted model.

This is continuous bounded statistical feature/code generation over available
inputs. It does not discover or purchase new data sources, implement arbitrary
new model families, promote unqualified models, or supply missing original
pregame observations. A general-purpose unattended engineering agent with
scoped credentials, an isolated work queue, independent review, rollback,
spend controls, and an emergency stop remains a separate implementation task.
