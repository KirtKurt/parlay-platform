# MLB V15.11.1 deployment release marker

This marker starts one clean deployment attempt for the merged historical daily-slate optimizer.

Authorized by this release:

- deploy the fail-closed historical authority guard to the existing MLB runtime;
- deploy or update the separate historical optimizer stack;
- build and persist the complete no-paid-call historical request plan;
- verify the 1,000-game training minimum, 15-minute snapshots beginning at 01:00 America/New_York, per-game T-45 clipping, complete-slate coverage, and the 80% daily held-out release gate.

Not authorized by this release:

- paid The Odds API historical calls;
- promotion without real walk-forward and untouched-audit evidence;
- production cutover before every mandatory daily gate passes;
- automatic wagering;
- fallback restoration of a retired legacy authority after a future qualifying cutover.

V15.10 remains the incumbent prediction authority until V15.11.1 produces a checksum-bound real-data champion that satisfies every production gate. This marker does not claim that the 80–90% objective has already been achieved.
