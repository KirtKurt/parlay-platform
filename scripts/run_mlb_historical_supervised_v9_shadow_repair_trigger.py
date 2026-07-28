"""One-time trigger for fresh V7/V9 shadow evidence regeneration.

The authoritative workflow owns evaluation and publication. This marker is inert at
runtime and exists only to trigger the workflow path filter after a zero-byte report
was detected on main.
"""

REPAIR_TRIGGER = "2026-07-28-v9-evidence-regeneration"
