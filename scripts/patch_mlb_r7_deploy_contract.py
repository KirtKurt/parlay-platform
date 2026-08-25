#!/usr/bin/env python3
"""Replace stale V15.10 deploy assertions with the R7 authority verifier."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


CAPACITY_PATTERN = re.compile(
    r'''(?m)^            curl --fail --silent --show-error \\
              --connect-timeout 2 --max-time 8 \\
              "\$\{base_url\}/v1/mlb/model/version" >/tmp/mlb-capacity-model\.json \\
              && model_ok=true$'''
)
CAPACITY_REPLACEMENT = '''            python scripts/verify_mlb_authority_response.py \\
              --url "${base_url}/v1/mlb/model/version" \\
              --output /tmp/mlb-capacity-model-contract.json \\
              --request-timeout-seconds 8 \\
              --quiet \\
              && model_ok=true'''

SMOKE_PATTERN = re.compile(
    r'''(?ms)^          python scripts/mlb_deploy_http_probe\.py \\
            "\$\{base_url\}/v1/mlb/model/version" \\
            --output /tmp/mlb-model-version\.json \\
            --max-wait-seconds 180 \\
            --request-timeout-seconds 20\n          python - <<'PY'\n.*?^          PY\n\n(?=      - name: Smoke test read-only MLB lock status)'''
)
SMOKE_REPLACEMENT = '''          python scripts/verify_mlb_authority_response.py \\
            --url "${base_url}/v1/mlb/model/version" \\
            --output /tmp/mlb-model-authority-contract.json \\
            --request-timeout-seconds 20

'''

FORBIDDEN = (
    "Live MLB V15.10 authority contract is stale",
    "Live MLB V15.10 runtime contract is stale",
    "mlb_ranked_winner_v15_10_active_ensemble",
    "INQSI-MLB-v5.0-ranked-winner-v15.10-active-ensemble",
)


def patch_text(text: str) -> str:
    patched, capacity_count = CAPACITY_PATTERN.subn(CAPACITY_REPLACEMENT, text)
    if capacity_count != 1:
        raise ValueError(
            f"expected one stale capacity authority probe, found {capacity_count}"
        )
    patched, smoke_count = SMOKE_PATTERN.subn(SMOKE_REPLACEMENT, patched)
    if smoke_count != 1:
        raise ValueError(
            f"expected one stale V15.10 smoke contract, found {smoke_count}"
        )
    residual = [value for value in FORBIDDEN if value in patched]
    if residual:
        raise ValueError(f"retired deploy assertions remain: {residual}")
    if patched.count("verify_mlb_authority_response.py") != 2:
        raise ValueError("expected exactly two R7 authority verifier calls")
    return patched


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path", nargs="?", default=".github/workflows/deploy.yml"
    )
    args = parser.parse_args()
    path = Path(args.path)
    original = path.read_text(encoding="utf-8")
    patched = patch_text(original)
    path.write_text(patched, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
