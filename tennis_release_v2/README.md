# Tennis MLB exact-parity V2 deployment payload

This content-addressed release updates only the isolated `parlay-platform-tennis-ml-prod` AWS stack.

It repurposes the production MLB V2.1 market calculation sequence for Tennis while retaining Tennis-only runtime modules, tables, locks, outcomes, archives, schedules, model registry, and trained artifacts. The release adds:

- all active Tennis competition discovery and all event-roster matches;
- dynamic collection at the prior 15-minute heartbeat no later than earliest match minus 10 hours;
- canonical 15-minute history and source-locked MLB de-vig, movement, reversal, edge, EV, score, and guardrail calculations;
- one immutable T-minus-45 lock per match using the last persisted pre-cutoff prediction without rescoring;
- exact provider-key plus event-id settlement;
- autonomous Tennis-only challenger training, validation, registration, and promotion;
- existing provider credential fallback injected only as `TENNIS_ODDS_API_KEY`;
- MLB CloudFormation fingerprint protection before and after deployment.

The workflow deploys disabled first, performs a forced live all-Tennis pull and prediction smoke, then enables the four Tennis schedules only after every check passes.
