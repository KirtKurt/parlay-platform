# Tennis V2.4 — Polymarket full-slate market adapter with MLB process-flow parity

Release archive SHA-256: `80e80feae7f675683d305418b95bc7a7815f764079d5fec577a357e2b0140a29`

Source-tree SHA-256: `8b8d9d07d8440bde956b1441d55b5daf7db632aded18c310ca7f5c81360ef037`

This content-addressed release repairs the live zero-card failure by replacing the inactive legacy Tennis tournament catalog with public Polymarket Tennis moneyline discovery and paired CLOB order books. The Tennis runtime preserves the source-pinned MLB calculation sequence, dynamic earliest-match-minus-ten-hours collection, canonical 15-minute history, immutable no-rescore T-minus-45 locks, exact-event settlement, and Tennis-only autonomous ML.

No MLB runtime source, table, schedule, lock, outcome, model artifact, or data is used by Tennis. The deployment fingerprints the production MLB stack before and after the isolated Tennis update.
