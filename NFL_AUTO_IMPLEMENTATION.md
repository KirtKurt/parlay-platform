# NFL Auto implementation bundle

This directory is a repository-root overlay for `KirtKurt/parlay-platform`.

Included:

- `nfl_auto/` — isolated provider clients, feature engineering, historical backfill, bounded Bedrock analyst, model training, live transition, and Lambda handlers.
- `nfl-auto-template.yaml` — dedicated AWS SAM stack.
- `.github/workflows/deploy-nfl-auto.yml` — verification and deployment workflow.
- `tests/nfl_auto/` — leakage, isolation, provider-market, gate, model, quota, and adaptive-learning tests.
- `docs/NFL_AUTO_RUNBOOK.md` — operating and deployment runbook.

No existing repository file is overwritten by this patch.
