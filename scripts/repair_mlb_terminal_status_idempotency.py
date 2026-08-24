#!/usr/bin/env python3
"""Treat only non-semantic official terminal metadata drift as idempotent.

MLB Stats API may first expose a completed game as Game Over/O and later as
Final/F.  The raw official payload fingerprint consequently changes even when
teams, scores, winner, immutable prediction identity, and training facts do not.
Those two audit metadata fields must not be classified as immutable settlement
facts.  They remain stored and covered by each row's original write-once
settlement fingerprint; this repair changes only collision comparison.
"""

from __future__ import annotations

from pathlib import Path


PATH = Path("hello_world/mlb_canonical_final_labels_v1.py")


def main() -> int:
    source = PATH.read_text(encoding="utf-8")
    original = source
    for line in (
        '    "source_payload_fingerprint",\n',
        '    "official_status",\n',
    ):
        marker = "IMMUTABLE_SETTLEMENT_FACT_FIELDS = ("
        start = source.index(marker)
        end = source.index(")\n\n\ndef _immutable_settlement_facts", start)
        block = source[start:end]
        if line in block:
            block = block.replace(line, "", 1)
            source = source[:start] + block + source[end:]

    if source != original:
        PATH.write_text(source, encoding="utf-8")
        print("Removed non-semantic terminal audit metadata from immutable collision facts")
    else:
        print("Terminal status idempotency repair already applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
