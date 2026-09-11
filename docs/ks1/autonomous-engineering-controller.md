# MLB autonomous engineering controller v1

The controller runs hourly and selects the highest-priority incomplete MLB research task. Completion is durable: a task is skipped only after its unique completion marker exists on `main`. An open `[MLB AUTO]` pull request blocks duplicate work.

The coding authority is AWS Bedrock invoked directly from a fixed allow-list; the worker does not require permission to list Bedrock models. If no model can be invoked, it fails closed and records no code change.

Generated edits are limited to `mlb_research/`, `tests/unit/test_mlb_research_*`, and `docs/ks1/`. Production KS1 serving, prediction locks, calibration/promotion gates, AWS/IAM, workflows, other sports and secrets are denied. The generated patch is capped by file count and bytes, must include focused tests and a task marker, and must pass controller policy tests plus the existing research/provenance suite before a PR is opened.

Every change is made on an isolated branch. The controller requests GitHub auto-merge, but repository checks/branch policy remain authoritative; if auto-merge is unavailable the PR remains gated rather than bypassing safety.

This is intentionally not an unrestricted self-modifying production agent. It is an unattended research-code worker that can continuously advance the MLB challenger backlog while production authority remains separately qualified.
