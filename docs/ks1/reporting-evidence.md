# KS1 reporting evidence

The existing forensic-consideration artifact now includes `reporting_evidence`
on every game. This is a read-only interpretation layer over retained prediction
rows, not a publication receipt, a prediction, or proof that a game is locked.
Require successful S3 readback and immutable pre-cutoff evidence separately.

Use this view when writing cards and alerts:

- `flag.side` describes signal direction. It does not identify the observed
  pitcher. Read `observation_subject` and its stable-ID validation instead.
  A selected-starter deterioration flag points toward the opposing team, but
  the deteriorating pitcher belongs to the selected team. For example, Gore's
  deterioration is adverse evidence for Texas, not evidence supporting Texas.
- `subject_population=team_starter_history` describes the team's rotation
  history. Do not attribute that feature to today's named starter.
- SHAP scores are retained toward home and separately oriented toward the
  selected team. Only verified, nonzero contributions claim actual consumption;
  they remain contribution evidence, not causal explanations.
- Serving WHIP and K-BB% fields use league-prior shrinkage. The view names them
  as model estimates and never labels them exact raw seven-day statistics.
  No appearances, an appearance with zero outs, missing data, and zero earned
  runs with positive innings remain distinct. Missing ER counts are not
  reverse-engineered from a rounded ERA.
- Odds require the row's exact retained `odds_event_id`, matching teams and
  start-time cross-check. Team-pair-only matching is forbidden for doubleheaders.
  Quotes or cache receipts newer than the prediction or T-10 cutoff are not
  presented as pregame prices. Each of the six requested books is explicitly
  unavailable when its eligible retained quote is absent. Post-lock facts can
  be reported separately but cannot replace locked evidence.
- `calibration_method` comes from the prediction row. A retained Platt artifact
  does not establish that Platt is the active calibration method.

The original detector flags/severity, feature formulas, probabilities, profile
hashes, model references, workflow cadence, AWS writes and lock rules are
unchanged. Historical detector consumers still call `evaluate()` unchanged;
the reporting view is attached only when building the daily artifact.
