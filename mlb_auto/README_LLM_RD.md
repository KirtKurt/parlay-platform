# MLB Auto LLM R&D Layer

This layer is scoped only to `mlb_auto`.

It uses Amazon Bedrock to propose new pregame numeric feature interactions from an allow-listed transform library. It cannot execute generated code, access postgame fields, alter T-45 lock rules, weaken chronological validation, or touch Tennis/legacy MLB stacks.

Candidates remain development-only until the existing MLB Auto training pipeline evaluates them. A candidate feature program becomes active only when the challenger using it passes the existing untouched chronological audit and is promoted as champion. Historical backfill periodically triggers research when due; status exposes the R&D state.
