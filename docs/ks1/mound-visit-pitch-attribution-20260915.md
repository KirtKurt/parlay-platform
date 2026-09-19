# Mound-visit pitch-attribution recovery

Trusted-main qualification run
[34935005515](https://github.com/KirtKurt/parlay-platform/actions/runs/34935005515)
left April 10, 2025 unqualified with
`unexpected substitution in automatic count-event proof`. Its retained report
identifies `sources/statcast-v2/2025-04-10.json` as the rejected candidate.

The public source replay isolates one candidate group: game 778382, at-bat 67.
[MLB's official play](https://statsapi.mlb.com/api/v1.1/game/778382/feed/live)
contains a zero-count mound visit, a zero-count pitching substitution to pitcher
696270, an automatic ball, and a tracked ball in play by batter 664040. Savant
attributes both count events to that same pitcher and batter, but incorrectly
retains `89.2` as release speed on the automatic ball. The final play is a
completed field out. No batter substitution or mid-count pitching change is
present.

V9 permits the pitching change proof when every preceding event is specifically
a non-pitch, non-substitution `mound_visit` action at a 0-0 count, carrying no
pitch data or pitch number, starting no later than the pitching change, and
ending by the end of the first subsequent count event. Those
administrative events do not advance count-event chronology because MLB's
mound-visit end timestamp overlaps the separately timestamped pitching change.
The pitching substitution itself and every subsequent automatic or physical
pitch remain strictly chronological. The incoming pitcher, position, 0-0 count,
physical pitch number/type/speed, batter, terminal outcome, full-game official
pitch counts and batter plate-appearance totals remain mandatory.

V8 and older retained artifacts keep their original policy and cannot gain this
case. Raw Savant values remain immutable; the only proposed canonical change is
clearing the automatic ball's contradictory `release_speed` after the complete
official count-event sequence is proven.

## Evidence and replay

- Qualification artifact `10383726161`, GitHub artifact digest
  `sha256:3dd83767adcf3fe30a4aabfe623acdf3f24c2afc7d7eac42838cfda3a9ca143b`.
- Public April 10 Savant CSV SHA-256:
  `b3e5823a2b30aec60a250c153b9dbd7205095f889677d6ef208d6a2c1e47de80`.
- Public full official game 778382 feed SHA-256:
  `1f9c523ec9e21f0c13210cf98c0fb05daf77713eaa6c7c76f69e341294b1b36d`.
- The replay finds exactly one automatic-event candidate group on the date,
  reproduces the V8 rejection, and produces exactly one V9 derivation:
  game 778382, at-bat 67, pitch 1, `release_speed: 89.2 -> ""`.
- Public-feed replay is not retained hosted qualification. A trusted-main run
  must store and read back exact source versions and pass the unchanged complete
  physical, PA, outcome, provenance, development and model gates.

No model parameters, data split, frozen 300-game retrospective holdout, model
reference, AWS writer authority, T-10 behavior, grading lineage or 02:00 ET
audit schedule changes here.
