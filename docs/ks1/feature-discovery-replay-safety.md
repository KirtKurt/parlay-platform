## Frozen replay and final-refit resilience

Frozen v1 artifacts use the retained `mlb_research_feature_replay_v1.py`
interpreter/compiler and v1 bounds. Inference never dispatches to the current
discovery generator. Later generator/limit changes cannot invalidate a v1
prospective test; new semantic versions need a separate replay implementation.
Unknown versions still fail closed, and stored recipe/source hashes are checked.

Final fitting uses development data only. If a configuration cannot fit the
larger final development fold, its error is recorded in `finalFitFailures` and
the next chronologically validated eligible configuration is attempted before
any holdout scoring. Only the first successfully fitted choice sees the holdout.
Poor holdout results never cause another candidate to be tried. If all refits
fail, research remains blocked rather than emitting an invalid model.
