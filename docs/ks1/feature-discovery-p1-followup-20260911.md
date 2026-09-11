# Feature-discovery P1 follow-up — 2026-09-11

PR #721 merged before two review findings were incorporated. This follow-up branch contains only the post-merge corrections:

1. The daily MLB data-expansion workflow installs `mlb_research/requirements.txt` before importing the NumPy-dependent feature-discovery modules and tracks all `mlb_research/**` changes.
2. Feature-discovery artifact/latest S3 failures are contained inside the sidecar boundary and cannot propagate into canonical research dataset publication or suppress downstream KS1 refreshes.
3. Regression tests cover artifact-write failure, latest-pointer failure, scheduled runtime dependencies, and the existing holdout/promotion isolation contracts.

These corrections do not change serving predictions, model authority, qualification thresholds, lock timing, or supported markets.
