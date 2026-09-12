"""Checksum helpers shared by asset download and environment validation."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: str | Path) -> str:
    """Return the lowercase SHA-256 digest for one file."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file_sha256(path: str | Path, expected: str) -> None:
    """Raise when a file is missing or does not match its declared digest."""

    target = Path(path)
    normalized_expected = str(expected).strip().lower()
    if not normalized_expected:
        return
    if not target.is_file():
        raise RuntimeError(f"checksum target is missing: {target}")
    actual = sha256_file(target)
    if actual != normalized_expected:
        raise RuntimeError(
            f"checksum mismatch for {target}: expected {normalized_expected}, got {actual}"
        )
