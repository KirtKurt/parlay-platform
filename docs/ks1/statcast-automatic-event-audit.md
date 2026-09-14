# Automatic events and thrown-pitch completeness

Investigation on September 14, 2026 found that the exact pitch-count gate
compared every Savant event with the official count of physically thrown
pitches. This rejected complete daily archives, preventing contiguous batter
and matchup windows from entering training.

## Independently reproduced primary-source example

Official game 778563, Dodgers at Cubs, March 18, 2025:

| Quantity | Count |
| --- | ---: |
| Savant rows for the game | 307 |
| Physical pitches in official pitcher boxes | 306 |
| Savant rows for Yamamoto (808967) | 73 |
| Yamamoto's official `numberOfPitches` | 72 |
| Automatic-ball events for Yamamoto | 1 |

The extra row has `description=automatic_ball`, empty `pitch_type` and
`release_speed`, batter 664023, at-bat 4, pitch number 1. The corresponding
official play event is `isPitch=false`, `details.code=VP`, with a pitcher
pitch-timer violation. Excluding that event from physical pitch counts gives
exact equality for every pitcher in the game; no count tolerance is needed.

The same date's game 778921 provides an independent automatic-strike example:
at-bat 65, batter 815697, pitcher 592826. Savant reports `automatic_strike`
with empty pitch type and speed; the official event has `isPitch=false`,
`details.code=AC`, and a batter pitch-timer violation.

Sources accessed directly:

- [Official game 778563 feed and box scores](https://statsapi.mlb.com/api/v1.1/game/778563/feed/live)
- [Official game 778921 feed](https://statsapi.mlb.com/api/v1.1/game/778921/feed/live)
- [Savant March 18 event CSV](https://baseballsavant.mlb.com/statcast_search/csv?all=true&type=details&game_date_gt=2025-03-18&game_date_lt=2025-03-18&group_by=name&player_type=pitcher)
- [Savant field definitions](https://baseballsavant.mlb.com/csv-docs)

## Correction and limits

`is_thrown_pitch` excludes only the two documented automatic descriptions
when pitch type and speed are empty. Unknown or untracked pitches still count.
Contradictory tracking stays in the count and cannot silently qualify a day.
Every raw event still undergoes game, pitcher, and duplicate-identity checks.
Physical counts must still equal every official pitcher's count exactly.

Review hardening also reconciles terminal plate appearances independently
against official `battersFaced`. A lost automatic walk/strikeout cannot qualify
merely because all physical pitches remain. Each game must have the exact
official PA total and unique terminal at-bat identities; missing official
counts fail closed. Sacrifice bunts and other non-wOBA PAs still count, while
baserunning outs do not. PA totals are game-level because inherited counts
can assign the official batter faced to an earlier pitcher. The retained
coverage method is `official_box_thrown_pitches_and_pa_v3`.

The real 778563 example independently reconciles 306 physical pitches and
74 terminal PAs against 74 official batters faced. Synthetic regression tests
remove only an automatic terminal outcome, preserving all physical pitches,
and verify that the date is rejected. A verified zero-physical-pitch reliever
appearance retains its automatic PA outcome; pitch-denominator metrics stay
null instead of dividing by zero or inventing a physical observation.

The terminal-code set was audited against MLB's official
[event-type catalog](https://statsapi.mlb.com/api/v1/eventTypes) on September 14,
2026, including batter/fan interference and alternate strikeout terminal codes.
Pending scoring rulings fail closed. Every wOBA-eligible terminal must retain
`woba_denom=1` and a finite, nonnegative `woba_value`; explicit denominator-zero
exceptions are intentional walks, catcher interference, and sacrifice bunts.
Missing automatic outcome fields cannot shrink the sample silently.

Prior-year profiles independently run the same physical-count, PA-count and
outcome-field gate on every contributing game before aggregating any pitcher.
One unverified appearance rejects that pitcher's entire prior-year profile,
including a zero-pitch appearance, instead of publishing a partial-season prior.

The stricter field-level audit found two `field_out` rows with missing
`woba_denom` in the retained 778563 sample. Its physical/PA totals reconcile,
but it now correctly fails full outcome qualification until complete provider
fields are available. The 778921 sample also has missing denominator fields;
it illustrates automatic-event classification, not qualified regular-season
training coverage. No missing fields were synthesized to make either pass.

All raw events remain available for plate-appearance outcomes, including an
automatic event ending a walk or strikeout. Starter, bullpen, and batter
pitch-based denominators and starter pitch mixes use thrown pitches. Overall
and handedness wOBA/xwOBA use complete plate-appearance outcomes. An automatic
event has no pitch type to assign to a pitch-specific matchup.

Source hashes, version receipts, calendar-window completeness, chronological
cutoffs, historical/prospective distinctions, and the fixed model comparison
remain unchanged. The repair does not rewrite archived observations or activate
a model. Qualification must separately demonstrate learned value influence
and better Brier with no worse log loss on the same final 300 games.
