"""Integrity helpers for trusted, repository-controlled Pitcher Ks artifacts."""

from __future__ import annotations

from hashlib import sha256
from hmac import compare_digest
from pathlib import Path


def checksum_path_for(artifact_path: Path) -> Path:
    return artifact_path.with_suffix(".sha256")


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_artifact_checksum(artifact_path: Path) -> Path:
    checksum_path = checksum_path_for(artifact_path)
    checksum_path.write_text(
        f"{sha256_file(artifact_path)}  {artifact_path.name}\n",
        encoding="ascii",
    )
    return checksum_path


def verify_artifact_checksum(artifact_path: Path) -> str:
    if not artifact_path.is_file():
        raise RuntimeError(f"Pitcher Ks artifact is missing: {artifact_path}")

    checksum_path = checksum_path_for(artifact_path)
    if not checksum_path.is_file():
        raise RuntimeError(f"Pitcher Ks checksum is missing: {checksum_path}")

    tokens = checksum_path.read_text(encoding="ascii").strip().split()
    expected = tokens[0].lower() if tokens else ""
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise RuntimeError(f"Pitcher Ks checksum is invalid: {checksum_path}")

    actual = sha256_file(artifact_path)
    if not compare_digest(actual, expected):
        raise RuntimeError(
            "Pitcher Ks artifact checksum mismatch; refusing to deserialize it."
        )
    return actual
