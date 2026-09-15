# Proven unfinished at-bat endings

[Diagnostic run 34925147690](https://github.com/KirtKurt/parlay-platform/actions/runs/34925147690) retained two dates through the existing read-only diagnostic. ZIP SHA256: `dfb6ad671c8ed1779e4d33d88a6840164552ca1563357b2ba28f3fa0015f361e`.

| Date | Raw SHA256 | Official ending |
| --- | --- | --- |
| July 24 | `045dd82ce33d7052c70da16222c14fa9342f17506a7c9165ad9e2d5a8da11f60` | Game 823680 AB35: Tommy White was out at home on a wild pitch; Henry Bolte's count remained 2–2. |
| May 23 | `3e8bb0e351281fad6ff183bea88b01276b2c7cea036b7866415d5d28d1720194` | Game 824109 AB35: Cole Young was out at third on a wild pitch; Jhonny Pereda's count remained 2–2. |

Official feeds: [823680](https://statsapi.mlb.com/api/v1.1/game/823680/feed/live), [824109](https://statsapi.mlb.com/api/v1.1/game/824109/feed/live).

Both feeds show two prior outs, a single non-batter runner's third out on the terminal pitch, an incomplete batting count, and the next completed play in the next half-inning. The raw terminal pitch number, tracked type, and rounded speed match. Method v7 requires this entire proof, retains it under a new versioned `official-inning-ending-v1` source namespace, and reproduces it on each read. V1–v6 schemas and methods remain unchanged.

The derived group gets zero wOBA denominator and a proof-bound null PA credit. Raw event labels, players, weights, and physical pitches remain intact. Neither a detached derived row nor an invented event marker can remove a batter PA. Cached rich evidence remains directly reusable with the provider offline.

The July 24 full-day replay passes physical and outcome checks. May 23's wild-pitch ending also passes the new proof, but the full date remains rejected: game 824674 AB39 has a separate two-strike substitute strikeout. Rule 9.15(b) distinguishes that credit from a substitute's non-strikeout outcome; this change does not guess or rewrite it.

Public-feed local replay receipts are marked `LOCAL-REPLAY-NOT-RETAINED`. They establish debugging reproducibility, not hosted source retention or qualification. The trusted-main run must retain/re-read exact versions and pass the existing admission gates. No serving reference, promotion threshold, development/qualification split, or workflow permission changes.
