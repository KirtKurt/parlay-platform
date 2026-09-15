# Two-strike substitute strikeout recovery

May 23 remained rejected after v7 because game 824674, at-bat 39 contains
two batters. Statcast and the official terminal play identify batter 701305,
but the preceding two pitches and the 0-2 offensive substitution identify
predecessor 670541. The final official box charges the predecessor three
plate appearances and one strikeout; the substitute has one plate appearance
and no strikeouts elsewhere in that game.

[MLB's 2026 Official Baseball Rules, 9.15(b), printed page 133](https://mktg.mlbstatic.com/mlb/official-information/2026-official-baseball-rules.pdf#page=145)
assigns the strikeout and at-bat to the original batter when a substitute
inherits two strikes and completes a strikeout. The official pitch source is
[game 824674](https://statsapi.mlb.com/api/v1.1/game/824674/feed/live).

V8 accepts this credit only after matching the inherited count independently
on the preceding physical pitch, the offensive substitution identities,
every pitch's order/type/speed/player and chronology, and the terminal third
strike and completed-play count/time. The raw terminal row must already carry
zero wOBA weight and unit denominator. Raw batter/pitcher IDs, outcomes,
measurements and weights remain unchanged. The derived PA-credit map is
reproduced from the exact retained raw/pitch versions on read; the complete
day must still match every official pitcher pitch count and batter PA total.
V1 through v7 retain their original policies and cannot accept this new case.

## Evidence and replay

- Retained diagnostic: run `34925147690`, artifact `10379920833`, ZIP SHA-256
  `dfb6ad671c8ed1779e4d33d88a6840164552ca1563357b2ba28f3fa0015f361e`.
  ZIP hash and extracted bytes verified against GitHub's artifact digest.
- May 23 raw SHA-256:
  `3e8bb0e351281fad6ff183bea88b01276b2c7cea036b7866415d5d28d1720194`;
  retained source version `Bapk3lQeMWxTyMrfqZwoQihabbHTU7Fl`.
- Official pitch-source data SHA-256 for game 824674:
  `07be64eb0fba664cefd9dc680fda35d32a034c36424e0cc163fdbdeb8cdbd08a`.
- May 23 full-day replay passes both physical and outcome validation with
  credited batter `670541` for at-bat `39`. July 24 remains passing.
  Local public-feed receipts are explicitly `LOCAL-REPLAY-NOT-RETAINED`;
  this replay does not claim hosted qualification or new source retention.
- Regression coverage includes exact source-version deletion, offline cache
  reuse, contradictory counts/player/speed/timestamps, altered derived credit,
  conflicting official box totals, and v6/v7 compatibility.

Existing trusted-main source-write authorization, recovery budget, 300-row
feature floor, frozen qualification cohort, Brier/log-loss and substantive
feature-usage requirements, T-10 behavior, and serving references are unchanged.
