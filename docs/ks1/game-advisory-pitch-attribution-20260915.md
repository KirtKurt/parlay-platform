# MLB game-advisory pitch-attribution evidence (2026-09-15)

## Scope

This evidence supports one narrow extension of the retained official pitch-attribution
policy. It does not alter a provider observation, infer an outcome, or relax the
independent whole-game plate-appearance, pitch-count, batters-faced, and terminal-row
checks.

## Independently fetched sources

- Baseball Savant `2025-05-07` CSV SHA-256:
  `e7159cac21648d6dec3d96bcfde0b223f64462e3f08da5f5cbf2564e2039ce59`
- MLB Stats API live feed for game `778023` SHA-256:
  `6dfe59b45b4f368ac6a4c5d7d879f0af1ae79bea6f6cdee28d6fa7e54f1d7785`
- Official scheduled start: `2025-05-07T23:15:00Z`

The first official plate appearance is complete and has `atBatIndex: 0`. Its first
three play events are non-pitch, non-substitution `game_advisory` actions with zero
balls, strikes, and outs:

1. Pre-Game: `20:05:45.749Z` to `22:51:38.876Z`
2. Warmup: `22:51:38.876Z` to `23:14:02.337Z`
3. In Progress: `23:14:02.337Z` to `23:15:52.790Z`

The chain is contiguous, brackets the scheduled start, and ends exactly when the
first official physical pitch begins. None of the advisory events has pitch data or
a pitch number.

Savant and MLB then agree on the complete six count-event sequence: four physical
pitches, an automatic ball, and a final physical pitch. Savant incorrectly associates
release speed `83.4` with the automatic ball. The retained MLB event is `isPitch:
false`, `type: no_pitch`, has automatic-ball code `VP`, and contains no pitch data.

## Replay result

- Policy v9 rejects the source with `official pitch-event chronology invalid` because
  the administrative events predate the scheduled start.
- The first v10 main replay (`34980534826`) still failed closed because its filtered
  request retained the advisory descriptions but omitted `count.outs`. That was a
  source-contract defect, not permission to assume a zero count.
- Policy v11 requests and retains both the exact descriptions and all three count
  fields in a new content-addressed namespace. The filtered endpoint independently
  returns `balls: 0`, `strikes: 0`, and `outs: 0` for each advisory. V11 then derives
  only the already-supported changes for the game, including clearing the automatic
  ball's impossible release speed. The raw Savant object remains unchanged.
- Retained v1-v10 evidence remains replayable against its original endpoint and
  namespace; no prior source object is upgraded or reinterpreted as v11 evidence.
  V11 uses a distinct count namespace for pitch, taxonomy, and inning objects;
  even the URL-identical inning projection cannot reuse a weaker v10 cache entry.
- Negative tests reject a non-first plate appearance, altered count, pitch or
  substitution flags, pitch data, pitch number, wrong event type or count, broken
  continuity, late ending, and a chain that does not bracket scheduled start.

The source-specific result authorizes the reusable shape check, not a game-ID or date
exception. Any contradiction still fails closed.
