from __future__ import annotations

import base64
import gzip
import hashlib
import json
from pathlib import Path

MANIFEST = {
    "tennis_learning/live_pipeline.py": (
        "scripts/tennis_all_regions_payloads/live_pipeline.py.gz.b64",
        "6c59bdb2b12879db8df9215f2dae76a48fbda1e482441b1bbae3588ea4fcb9b8",
    ),
    "tennis-template.yaml": (
        "scripts/tennis_all_regions_payloads/tennis-template.yaml.gz.b64",
        "f53ab848098e520d80a7f965575781d0fed904d53eb3cdfecff4afb1f4f3354f",
    ),
    "tests/test_tennis_live_coverage_contract.py": (
        "scripts/tennis_all_regions_payloads/test_tennis_live_coverage_contract.py.gz.b64",
        "60024b0105445f0dc9bac62b4b4fb2c0b5741dc07ed4913509cae415385f6a3f",
    ),
    ".github/workflows/deploy-tennis-learning.yml": (
        "scripts/tennis_all_regions_payloads/deploy-tennis-learning.yml.gz.b64",
        "dc7efa4faf6a5c0c0acdd015f8501baaccaa4934ad0e49aaa7c72bc63b100010",
    ),
    ".github/workflows/publish-tennis-daily-card.yml": (
        "scripts/tennis_all_regions_payloads/publish-tennis-daily-card.yml.gz.b64",
        "ec4de6a4882ad559b08ff81de9cf4e4e4b760a56c120df5f70407d94e7a7d328",
    ),
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def install(root: Path) -> dict[str, str]:
    written: dict[str, str] = {}
    for destination, (payload_path, expected_sha256) in MANIFEST.items():
        encoded = (root / payload_path).read_text(encoding="utf-8").strip()
        data = gzip.decompress(base64.b64decode(encoded, validate=True))
        actual_sha256 = _sha256(data)
        if actual_sha256 != expected_sha256:
            raise RuntimeError(
                f"payload checksum mismatch for {destination}: "
                f"expected={expected_sha256} actual={actual_sha256}"
            )
        output = root / destination
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
        persisted_sha256 = _sha256(output.read_bytes())
        if persisted_sha256 != expected_sha256:
            raise RuntimeError(
                f"persisted checksum mismatch for {destination}: "
                f"expected={expected_sha256} actual={persisted_sha256}"
            )
        written[destination] = persisted_sha256
    return written


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    written = install(root)
    print(
        json.dumps(
            {
                "ok": True,
                "repair": "TENNIS_ALL_ACTIVE_MATCHES_ALL_REGIONS",
                "required_regions": ["us", "us2", "uk", "eu", "au"],
                "sport_keys_truncated": 0,
                "immutable_prediction_history_rewritten": False,
                "model_authority_changed": False,
                "human_winner_selection": False,
                "other_sport_changed": False,
                "written": written,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
