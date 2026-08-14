#!/usr/bin/env python3
"""Replace the obsolete manual-only MLB promotion invariant.

The replacement still requires the full immutable prospective trainer,
chronological untouched testing, calibration/proper-scoring gates, the V2
runtime consumer, and automatic wagering disabled. It changes only whether a
fully passing champion may be activated automatically.
"""
from __future__ import annotations

from pathlib import Path


PATH = Path("scripts/verify_mlb_schedule_invariants.py")


def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    old = (
        '    "INQSI_MLB_ML_AUTO_PROMOTE: \'false\'": '
        "'automatic MLB ML promotion must be disabled',\n"
    )
    new = (
        '    "INQSI_MLB_ML_AUTO_PROMOTE: \'true\'": '
        "'gated automatic MLB ML promotion must be enabled',\n"
        '    "INQSI_MLB_V2_INFERENCE_ENABLED: \'true\'": '
        "'MLB V2 gated inference consumer must be enabled',\n"
    )
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise RuntimeError("obsolete MLB promotion invariant marker not found")

    forbidden = (
        "if \"INQSI_MLB_ML_AUTO_PROMOTE: 'false'\" not in text:\n"
        "    violations.append('automatic MLB ML promotion must be disabled')\n"
    )
    if forbidden in text:
        text = text.replace(forbidden, "", 1)

    required_checks = '''if "INQSI_MLB_ML_AUTO_PROMOTE: 'true'" not in text:
    violations.append('gated automatic MLB promotion is not enabled')
if "INQSI_MLB_V2_INFERENCE_ENABLED: 'true'" not in text:
    violations.append('MLB V2 inference consumer is not enabled')
'''
    marker = 'if \'"days_ahead":0\' not in text and \'"days_ahead": 0\' not in text:\n'
    if "gated automatic MLB promotion is not enabled" not in text:
        if marker not in text:
            raise RuntimeError("MLB schedule invariant insertion marker missing")
        text = text.replace(marker, required_checks + marker, 1)

    PATH.write_text(text, encoding="utf-8")
    print("Replaced obsolete manual-only MLB promotion invariant.")


if __name__ == "__main__":
    main()
