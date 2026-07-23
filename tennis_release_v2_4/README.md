# Tennis V2.4 — Polymarket full-slate market adapter with MLB process-flow parity

This content-addressed release repairs the live zero-card failure by replacing the inactive legacy Tennis tournament catalog with public Polymarket Tennis moneyline discovery and paired CLOB order books. The independent Tennis runtime preserves the source-pinned MLB calculation sequence, dynamic earliest-match-minus-ten-hours collection, canonical 15-minute history, immutable no-rescore T-minus-45 locks, exact-event settlement, and Tennis-only autonomous ML.

No MLB runtime source, table, schedule, lock, outcome, model artifact, or data is used by Tennis. The deployment workflow fingerprints the complete production MLB stack before and after the isolated Tennis update.
