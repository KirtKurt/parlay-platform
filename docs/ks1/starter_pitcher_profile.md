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

Official results have separate `results_window_statuses` for 7-day, 30-day,
last-three-start and prior-year windows. A Savant delay no longer clears ERA,
WHIP, RA9, decisions, FIP or strikeout/walk counts when the official box-score
history is complete. Contact and pitch metrics keep their stricter independent
coverage checks; `window_statuses` still describes the combined profile.

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

On September 14, 2026, the user authorized verified historical point-in-time
values in place of waiting for 300 prospective games. Qualification now requires
the same exact trailing 300-game chronological holdout, with every learned
pitcher-context field covered by a validated historical manifest or an immutable
pregame profile. Training labels must predate the earliest holdout prediction.
Unverified values, postgame starter identities and missing feature values do not
qualify. Historical results remain labeled retrospective; prospective coverage
is reported separately. The holdout is not filtered to select easier games.

A candidate must beat the incumbent on Brier score without worsening log loss.
If it consumes batter or bullpen fields, those fields also need pregame evidence
on every holdout game. Unused batter/bullpen groups do not block a starter-only
candidate. Qualification produces experiment artifacts; serving still requires
a separately reviewed, checksum-bound model-reference promotion.
# Retrospective official pitcher reconstruction

`KS1-prior-pitcher-reconstruction-v1` extends the retained historical context
through the available official history. It binds each side to the versioned
MLB box-score source, effective input games, retrieval time, pitcher identity
mode, metrics, and checksum. Only a source with complete official year coverage
is eligible. Same-day, target, future, and later-resumed games are excluded.

Observed pregame pitcher IDs are preserved. Where historical pregame identities
were not retained, a fixed rotation projection uses the last 18 prior team games
within 120 days, selecting a prior starter with 4–10 days of rest closest to
five. These projected IDs remain in the reconstruction proof; they are never
reported as confirmed starter IDs. Missing projections and incomplete counts
remain unavailable. Official historical scoring corrections are possible.

The four available summary inputs use the same formulas as daily inference:
current-season negative FIP, K-minus-BB percentage, recent three-start command,
and expected innings from up to five starts. Velocity is left unavailable in
this official-only summary. This does not claim that historical Statcast or
proprietary pitching metrics have been recovered.

Qualification admits the independently validated proofs for both teams on the
unchanged trailing 300-game chronological test. The candidate must actually use
pitcher context in a tree split, improve Brier score, and not worsen log loss.
A fixed ablation without pitcher context is reported separately. Main-branch
repairs trigger isolated candidate validation and AWS artifact readback;
activation still requires a separate reviewed serving-reference change.
