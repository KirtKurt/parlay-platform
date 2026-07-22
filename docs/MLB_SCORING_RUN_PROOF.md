# MLB Scoring Run Proof

## Purpose

The MLB pull guard proves that canonical 15-minute odds slots were written. It does not, by itself, prove that downstream signal calculation and prediction persistence completed for every official game.

`MLB-SCORING-RUN-PROOF-v1-all-games-components-persisted` creates a separate immutable proof for each MLB game date and canonical pull slot.

## DynamoDB authority

Each proof is written to the snapshots table under:

```text
PK = SCORING_RUN#mlb#YYYY-MM-DD
SK = SLOT#<canonical UTC slot>
```

The item is conditional and write-once. A retry may return the existing item only when the proof fingerprint matches exactly. A same-slot payload mismatch fails with an immutable collision.

## Required completion contract

A scoring run passes only when all of the following are true:

- the official provider manifest has a positive game count;
- the canonical pull was stored successfully;
- movement-feature generation completed;
- the read-only hot-side stage completed;
- the game-winner stage completed;
- winner coverage equals the official manifest count;
- all official games received a prediction;
- pre-lock candidate and persisted-row counts equal the official manifest count;
- every prediction exposes market, movement, fundamentals, and final score components;
- every prediction exposes a predicted winner.

A failure proof is still persisted for diagnosis, then the protected scheduled invocation returns an error so EventBridge and CloudWatch observe the failure.

## Component visibility

Every game row records:

- market score;
- line-movement score;
- fundamentals score and whether live fundamentals were applied;
- ML score, mode, and whether ML had production authority;
- final ensemble score;
- component weights;
- calibration metadata;
- actionability, authority, tags, and source-pull metadata.

Missing fundamentals remain explicit as neutral or source-unavailable. The proof does not manufacture player, lineup, bullpen, injury, weather, or split data.

## Public read behavior

The existing read-only MLB endpoints include the latest proof for the requested date:

- `/v1/mlb/today`
- `/v1/mlb/games`
- `/v1/mlb/predictions`
- `/v1/mlb/game-winners`

Response fields include:

```json
{
  "scoringProofComplete": true,
  "scoringRunProofStatus": {},
  "scoring_run_proof": {}
}
```

The public Lambda does not recompute or write predictions. It only reads the persisted scoring proof and persisted prediction authority.

## Operational interpretation

A healthy production slate now requires two independent facts:

1. canonical pull coverage is complete; and
2. the latest scoring-run proof is `PASS` with counts matching the official game manifest.

This prevents a successful odds write from concealing a downstream scoring or prediction-persistence failure.
