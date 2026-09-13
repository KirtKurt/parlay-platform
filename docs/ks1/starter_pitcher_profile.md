# KS1 starter pitcher profile

This change expands the pregame starter profile without changing the accepted
KS1 model reference, calibration, T-10 cutoff, or production authority.

## Source roles

- The official MLB schedule/live feed owns game identity and the current
  probable-starter MLB player ID. A later verified pregame feed may replace or
  clear an earlier probable pitcher; a cleared pitcher is never resurrected.
- Big Balls Data (`BBS_API_KEY`) remains the independently matched MLB fixture
  and context source. Its public MLB player-stat coverage is partial, so absence
  is recorded and never converted to a zero or used to invent a starter.
- The Odds API (`ODDS_API_KEY`) independently binds the scheduled fixture and
  supplies fresh market context. It does not supply pitcher performance data.
- Baseball Savant supplies pitch-level contact, discipline, physics, and
  arsenal observations. Only retained pitches tied to completed games in the
  starter's admitted window are aggregated.
- Official completed game logs supply pitcher results and count statistics.

## Starter fields

Official game-log profiles are produced for 7 and 30 calendar days and the
last three starts. Existing 10/15/75-day fields remain for compatibility.

- Results: ERA, WHIP, RA9, wins, losses, decision win percentage.
- Defense independent: FIP using the published component formula and an
  explicit `fip_constant=3.10`; xFIP replaces home runs with retained Savant
  fly balls times the point-in-time league HR/FB rate. The constant and raw
  component counts are stored beside the values.
- Plate discipline: K%, BB%, and shrunk K-BB%, with batters faced and observed
  game counts retained.
- Contact: hard-hit rate, barrel rate, average exit velocity allowed, xwOBA on
  contact, and full plate-appearance xwOBA when every required Statcast value
  is present. Non-contact outcomes use their observed wOBA value; batted-ball
  outcomes use `estimated_woba_using_speedangle`.
- Pitch execution: swinging-strike rate and CSW rate.
- Physics: velocity, spin rate, horizontal movement, vertical movement, and
  extension. Movement is stored in inches from Savant's raw `pfx_x/pfx_z`; it
  is not mislabeled as proprietary Stuff+ or Location+.
- Arsenal: pitch mix plus per-pitch-type velocity, spin, movement, extension,
  whiff-per-pitch, whiff-per-swing, and xwOBA on contact. Valid types outside
  the stable serving taxonomy are retained in an explicit `other` bucket, so
  emitted mix shares still sum to 100%.
- Matchup: opposing confirmed-lineup left/right batting shares are paired with
  each starter profile.

Previous-season starter boxes and pitch traits are retained separately. The
30-day and previous-season velocity, spin, extension, four-seam velocity and
four-seam spin are also combined into transparent pitch-count-weighted talent
features; the previous-season contribution is capped at 300 pitches.
Expanded official boxes use a versioned cache and are never read through the
older projection that omitted hit batters and pitcher decisions. The source
job progressively fills previous-year and current-year caches; incomplete
windows remain explicitly fail-closed until both official-game and Statcast
coverage are proven.

Every new daily prediction stores a hash-bound `starter_profile_json` snapshot.
That record contains both starter IDs, all observed and unavailable metrics,
source roles, the feature cutoff, the retained-history timestamp, and separate
content and semantic hashes. Frozen T-10 rows keep their original profile.
The research T-10 snapshot also fingerprints the serving-compatible numeric
starter fields. Training-table rebuilds restore those immutable captured
values instead of recalculating them from a later rolling Statcast cache, so
prospective coverage cannot disappear as pitches age out.

## Explicitly unavailable fields

xERA, SIERA, Stuff+, Location+, Pitching+, and active-spin percentage remain
null unless an admitted provider publishes the exact metric for the exact
pregame window. KS1 does not relabel a home-grown proxy as a proprietary
metric. Raw velocity, spin, movement, extension, plate location and spin axis
remain retained so reviewed in-house models can be built later without
rewriting source history.

## Time safety and activation

Every game log must be independently tied to a completed official game before
the prediction cutoff. Same-day games remain excluded from the KS1 daily table,
which prevents game-one information leaking into game two of a doubleheader.
Research snapshots may include a same-day result only when its independently
recorded completion time is earlier than the snapshot.

These columns become eligible for candidate training only after sufficient
prospective starter coverage. They do not affect published probabilities until
a chronological candidate beats the incumbent on Brier score without worsening
log loss and its model reference is separately reviewed and promoted.
