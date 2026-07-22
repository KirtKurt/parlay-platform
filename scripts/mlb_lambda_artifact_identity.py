from __future__ import annotations

import base64
import hashlib
import json
import stat
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Tuple
from zipfile import BadZipFile, ZipFile


MANIFEST_SCHEMA_VERSION = "INQSI-MLB-LAMBDA-BUILD-MANIFEST-v1"
MAX_COMPRESSED_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_UNCOMPRESSED_ARTIFACT_BYTES = 512 * 1024 * 1024


def _content_record(path: str, content: bytes) -> Tuple[str, int, str]:
    return path, len(content), hashlib.sha256(content).hexdigest()


def _manifest(records: Iterable[Tuple[str, int, str]]) -> Dict[str, Any]:
    normalized = sorted(records, key=lambda item: item[0])
    if not normalized:
        raise ValueError("Lambda artifact contains no files")
    digest = hashlib.sha256()
    total_bytes = 0
    for path, size, content_sha256 in normalized:
        total_bytes += size
        digest.update(
            json.dumps(
                {
                    "path": path,
                    "size": size,
                    "sha256": content_sha256,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return {
        "contentManifestSha256": digest.hexdigest(),
        "fileCount": len(normalized),
        "uncompressedBytes": total_bytes,
    }


def directory_content_manifest(root: Path) -> Dict[str, Any]:
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f"Lambda build directory is missing: {root}")
    records = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Lambda build contains a symbolic link: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"Lambda build contains a non-file entry: {path}")
        relative = path.relative_to(root).as_posix()
        records.append(_content_record(relative, path.read_bytes()))
    return _manifest(records)


def zip_content_manifest(artifact: bytes) -> Dict[str, Any]:
    if not artifact:
        raise ValueError("Lambda deployment artifact is empty")
    if len(artifact) > MAX_COMPRESSED_ARTIFACT_BYTES:
        raise ValueError("Lambda deployment artifact exceeds the compressed size limit")
    records = []
    seen = set()
    total_bytes = 0
    try:
        with ZipFile(BytesIO(artifact), "r") as archive:
            for entry in archive.infolist():
                name = entry.filename
                if entry.is_dir():
                    continue
                if not name or "\\" in name:
                    raise ValueError(f"Lambda artifact has an invalid path: {name!r}")
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts or "." in path.parts:
                    raise ValueError(f"Lambda artifact has an unsafe path: {name!r}")
                normalized = path.as_posix()
                if normalized in seen:
                    raise ValueError(f"Lambda artifact has a duplicate path: {normalized}")
                seen.add(normalized)
                file_type = (entry.external_attr >> 16) & 0o170000
                if file_type == stat.S_IFLNK:
                    raise ValueError(
                        f"Lambda artifact contains a symbolic link: {normalized}"
                    )
                total_bytes += int(entry.file_size)
                if total_bytes > MAX_UNCOMPRESSED_ARTIFACT_BYTES:
                    raise ValueError(
                        "Lambda deployment artifact exceeds the uncompressed size limit"
                    )
                content = archive.read(entry)
                if len(content) != entry.file_size:
                    raise ValueError(
                        f"Lambda artifact size mismatch for {normalized}"
                    )
                records.append(_content_record(normalized, content))
    except BadZipFile as exc:
        raise ValueError("Lambda deployment artifact is not a valid ZIP file") from exc
    return _manifest(records)


def lambda_code_sha256(artifact: bytes) -> str:
    return base64.b64encode(hashlib.sha256(artifact).digest()).decode("ascii")
