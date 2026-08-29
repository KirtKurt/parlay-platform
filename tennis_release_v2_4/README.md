# Tennis V2.4 — Polymarket full-slate market adapter with MLB process-flow parity

This release repairs the live zero-card failure by replacing the inactive legacy Tennis catalog with public Polymarket match-moneyline discovery and paired CLOB order books.

## Content-addressed release

- Baseline Actions artifact ID: `8575790142`
- Baseline artifact SHA-256: `f8fe7f023794160a7ba7b4566fc4234bd91b852b13a6f49f14b35f5f06bbc74b`
- Overlay SHA-256: `325b77b554b5b3835176d8c93d930d8780c4093fb6ba3304f78f4c4770f1b17c`
- Reconstructed clean source-tree SHA-256: `885f7028b4d45755abfd72190b39e4c398900d808d21c0371db6308abdabe7cd`

The Tennis runtime preserves the source-pinned MLB calculation sequence, earliest-match-minus-ten-hours collection, canonical 15-minute history, immutable no-rescore T-minus-45 locks, exact-event settlement, and Tennis-only autonomous ML.

No MLB runtime source, table, schedule, lock, outcome, model artifact, or data is used by Tennis. Deployment fingerprints the production MLB stack before and after the isolated Tennis update.
