# KS1 seven-day candidate

The feature calendar now includes 7, 10, 30 and 75 days. Bullpen workload remains
1, 3 and 5 days. Existing same-day and later-completion exclusions, count-based
shrinkage, and T-10 lock preservation apply unchanged.

The fixed recent-data experiment trains before September 1, 2026 and evaluates
on labeled September games, with at least 500 training and 100 test games.
Only verified final-score rows qualify for this historical experiment; these
are never entered in the official prediction ledger. Parameters are unchanged
from the original LightGBM experiment. No holdout tuning or holdout refit occurs.
The incumbent and candidate are scored on identical games. A separately reported
ablation uses the same training period without seven-day features.

A candidate is eligible for the approved release only with strictly lower Brier
and no worse logloss than the incumbent. Accuracy and all three model metrics
are retained. This small historical holdout does not establish live accuracy.

Individual pitcher features require original pregame identities plus earlier
pitcher boxes. At least 100 training rows per side must have an observed starter
and positive prior 30-day batters faced. Otherwise individual features are
explicitly excluded and reported pending; actual postgame starter identities
are label-only and cannot fill that gap. No new data provider is contacted.

The PR job rebuilds from retained source objects, verifies the incumbent hash,
and saves an isolated experiment artifact with S3 readback. It never changes
serving references, predictions, locks, or calibration state. Release separately
pins the verified model hash/key/version in model_refs.json. Production metadata
reports learned feature names and whether individual starter features are used.
