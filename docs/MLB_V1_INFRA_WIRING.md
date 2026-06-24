# MLB v1 infrastructure wiring

The deploy workflow runs `scripts/patch_template_mlb_v1.py` before SAM validation and deployment.

The patcher updates the deployment template at deploy time to:

- add `RAW_ARCHIVE_BUCKET`
- add a retained encrypted S3 raw archive bucket
- add MLB v1 API routes:
  - `/v1/mlb/today`
  - `/v1/mlb/games`
  - `/v1/mlb/predictions`
  - `/v1/mlb/audit`
  - `/v1/mlb/model/version`
- add the MLB raw archive Lambda and 15-minute archive schedule
- remove legacy MLB fixed-time schedule blocks from the template before deploy

Runtime protection is also active in `mlb_manual_pull.py`: any non-HOT MLB pull is skipped and does not store a snapshot.
