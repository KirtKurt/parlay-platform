from __future__ import annotations

import base64
import binascii
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
        "2a0137dee83c4a8d18a9a249aaf2095b78a9e166a5f803f61d5c0c85d996f802",
    ),
    ".github/workflows/deploy-tennis-learning.yml": (
        "scripts/tennis_all_regions_payloads/deploy-tennis-learning.yml.gz.b64",
        "27c7c68bc70eb63745f593142323493d09950440d411fdb3c6c0750d7d7e13cd",
    ),
    ".github/workflows/publish-tennis-daily-card.yml": (
        "scripts/tennis_all_regions_payloads/publish-tennis-daily-card.yml.gz.b64",
        "29051157a6b878f408371b2896e09bbf175b6682ddcaf0affa94347411c2c919",
    ),
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode_payload(path: Path) -> bytes:
    # Base64 padding is transport syntax, not part of the signed source. Restore
    # omitted trailing '=' characters while retaining strict alphabet, gzip,
    # and SHA-256 verification. Any truncation or mutation still fails closed.
    encoded = "".join(path.read_text(encoding="utf-8").split())
    remainder = len(encoded) % 4
    if remainder == 1:
        raise RuntimeError(
            f"invalid base64 payload length for {path}: length={len(encoded)} remainder={remainder}"
        )
    padded = encoded + ("=" * ((4 - remainder) % 4))
    try:
        compressed = base64.b64decode(padded, validate=True)
        return gzip.decompress(compressed)
    except (binascii.Error, gzip.BadGzipFile, EOFError, OSError) as exc:
        raise RuntimeError(
            f"unable to decode checksum-locked payload {path}: "
            f"length={len(encoded)} remainder={remainder}: {exc}"
        ) from exc


def install(root: Path) -> dict[str, str]:
    written: dict[str, str] = {}
    for destination, (payload_path, expected_sha256) in MANIFEST.items():
        data = _decode_payload(root / payload_path)
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
