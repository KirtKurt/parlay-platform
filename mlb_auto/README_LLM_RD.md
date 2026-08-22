# MLB Auto LLM R&D Layer

This layer is scoped only to `mlb_auto`.

It uses Amazon Bedrock to propose new pregame numeric feature interactions from an allow-listed transform library. It cannot execute generated code, access postgame fields, alter T-10 lock rules, weaken chronological validation, or touch Tennis or legacy MLB stacks.

The effective runtime list is account-safe. By default it excludes non-Amazon provider models from the supplied list and normalizes Amazon Nova direct IDs to their US geographic inference-profile IDs. The first invokable effective model is used. Status exposes both the supplied list and the effective list, and model access is never reported as verified until a real research invocation succeeds.

When every effective model is temporarily unavailable because of a recognized quota, capacity, or account-access condition, the layer records `MODEL_PROVIDER_UNAVAILABLE` as retryable and degraded. It generates no candidate and does not block core MLB Auto ingest, historical backfill, training, prediction, locking, settlement, repair, or schedules. Configuration defects, malformed model responses, and feature-safety violations still fail closed as `LLM_RD_FAILED`.

Candidates remain development-only until the existing MLB Auto training pipeline evaluates them. A candidate feature program becomes active only when the challenger using it passes the existing untouched chronological audit and is promoted as champion.
