#!/usr/bin/env python3
"""Install the reviewed MLB competitive control-plane payload deterministically."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import os
import subprocess
import tarfile
from pathlib import Path

PAYLOAD_SHA256 = "68d80e40281c7a2f5fd50e8e58e65988d9234ab9a823fc3e77e2dfd17573d7da"
PARTS_DIR = Path("scripts/mlb_control_plane_payload")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    parts_dir = root / PARTS_DIR
    parts = sorted(parts_dir.glob("part*.txt"))
    if not parts:
        raise RuntimeError("MLB control-plane payload parts are missing")
    encoded = "".join(part.read_text(encoding="ascii").strip() for part in parts)
    payload = base64.b64decode(encoded.encode("ascii"), validate=True)
    actual = hashlib.sha256(payload).hexdigest()
    if actual != PAYLOAD_SHA256:
        raise RuntimeError(
            f"MLB control-plane payload checksum mismatch: expected={PAYLOAD_SHA256} actual={actual}"
        )
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for member in archive.getmembers():
            target = (root / member.name).resolve()
            if root not in target.parents:
                raise RuntimeError(f"unsafe bootstrap path: {member.name}")
            if not member.isfile():
                raise RuntimeError(f"unsupported bootstrap member: {member.name}")
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError(f"bootstrap member unreadable: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())
            os.chmod(target, member.mode)
    subprocess.run(
        [
            "python",
            str(root / "scripts" / "apply_mlb_competitive_control_plane.py"),
            "--root",
            str(root),
        ],
        check=True,
    )
    print(f"Installed MLB competitive control plane payload {PAYLOAD_SHA256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
