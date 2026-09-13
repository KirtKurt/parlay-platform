# KS1 seven-day candidate

The feature calendar now includes 7, 10, 30 and 75 days. Bullpen workload remains
1, 3 and 5 days. Existing same-day and later-completion exclusions, count-based
shrinkage, and T-10 lock preservation apply unchanged.

The recent-data experiment trains on games completed before a rolling holdout
and evaluates the latest 300 labeled games, ordered by official completion time,
with at least 500 earlier training games and 300 eligible test games. Rows with
labels completed at or after the first holdout prediction timestamp are excluded
from training, preventing overlapping-slate outcomes from leaking into the fit.
Only verified final-score rows qualify for this historical experiment; these
are never entered in the official prediction ledger. Parameters are unchanged
from the original LightGBM experiment. No holdout tuning or holdout refit occurs.
The incumbent and candidate are scored on identical games. A separately reported
ablation uses the same training period without seven-day features.

A candidate is eligible for the approved release only with strictly lower Brier
and no worse logloss than the incumbent. Accuracy and all three model metrics
are retained. A pitcher-aware candidate also must learn at least one verified
pitcher-context feature and all 300 holdout games must carry prospective pregame
pitcher context. Historical projections accelerate shadow learning but never
satisfy that prospective promotion requirement, even when the same row retains
other pregame evidence.

Individual pitcher features require original pregame identities plus earlier
pitcher boxes. At least 300 training rows per side must have an observed starter
and positive prior 30-day batters faced. Otherwise individual features are
explicitly excluded and reported pending; actual postgame starter identities
are label-only and cannot fill that gap. No new data provider is contacted.

The shadow trainer additionally reads the active checksum-bound V8 historical
context manifest. Only records that prove point-in-time construction, exclude
same-day results and target outcomes, preserve the immutable game/start/lock
binding, and identify their pitcher availability mode as `confirmed_archive` or
`strict_prior_projection` are admitted. The latter populate the shared pitcher
summary contract but remain explicitly distinct from observed starter identity.
Versioned KS1 prediction objects can supply real pregame starter IDs only when
S3 version history proves the unchanged row was stored no later than T-10.

The PR job rebuilds from retained source objects, verifies the incumbent hash,
and saves an isolated experiment artifact with S3 readback. It never changes
serving references, predictions, locks, or calibration state. Release separately
pins the verified model hash/key/version in model_refs.json. Production metadata
reports learned feature names and whether individual starter features are used.
