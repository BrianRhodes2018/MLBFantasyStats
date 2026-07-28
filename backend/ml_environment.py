"""Reproducibility metadata for the hit-model runtime."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from importlib import metadata
from pathlib import Path
from typing import Any, Iterable, Sequence

BACKEND_DIR = Path(__file__).resolve().parent
LOCK_PATH = BACKEND_DIR / "uv.lock"

SCIENTIFIC_DISTRIBUTIONS = (
    "numpy",
    "scipy",
    "pandas",
    "pyarrow",
    "polars",
    "scikit-learn",
)

MODEL_SOURCE_FILES = (
    BACKEND_DIR / "build_hit_dataset.py",
    BACKEND_DIR / "hit_calibration.py",
    BACKEND_DIR / "ml_environment.py",
    BACKEND_DIR / "predict_hits_today.py",
    BACKEND_DIR / "train_hit_model.py",
)


def sha256_file(path: Path) -> str | None:
    """Return a file's SHA-256 digest, or None when it does not exist."""
    if not path.is_file():
        return None

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_versions() -> dict[str, str]:
    """Return the installed versions that can materially affect model output."""
    versions: dict[str, str] = {}
    for distribution in SCIENTIFIC_DISTRIBUTIONS:
        try:
            versions[distribution] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            versions[distribution] = "missing"
    return versions


def dependency_fingerprint() -> tuple[str, str | None]:
    """Hash the Python contract, uv lock, and installed scientific versions."""
    lock_sha256 = sha256_file(LOCK_PATH)
    payload = {
        "python": f"{platform.python_version_tuple()[0]}.{platform.python_version_tuple()[1]}",
        "lock_sha256": lock_sha256,
        "packages": package_versions(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), lock_sha256


def code_commit() -> str | None:
    """Resolve the deployment commit without exposing repository paths."""
    for variable in ("RENDER_GIT_COMMIT", "GITHUB_SHA"):
        value = os.getenv(variable)
        if value:
            return value

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=BACKEND_DIR,
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def code_is_dirty() -> bool | None:
    """Report whether local code differs from the recorded Git commit."""
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=BACKEND_DIR,
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(completed.stdout.strip())


def json_fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_fingerprint(paths: Iterable[Path] = MODEL_SOURCE_FILES) -> str:
    """Hash model-source contents so uncommitted scheduled runs stay traceable."""
    manifest = [
        {"name": path.name, "sha256": sha256_file(path)}
        for path in sorted(paths, key=lambda item: item.name)
    ]
    return json_fingerprint(manifest)


def dataframe_fingerprint(frame: Any) -> str:
    """Fingerprint an ordered Polars frame without storing any player data."""
    digest = hashlib.sha256()
    schema = [(name, str(dtype)) for name, dtype in frame.schema.items()]
    digest.update(json.dumps(schema, separators=(",", ":")).encode("utf-8"))
    digest.update(frame.hash_rows(seed=0).to_numpy().tobytes())
    return digest.hexdigest()


def training_manifest(
    paths: Iterable[Path],
    row_count: int,
    frame_sha256: str | None = None,
) -> dict[str, object]:
    """Describe immutable training files without recording machine-specific paths."""
    files = [
        {
            "name": path.name,
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(paths, key=lambda item: item.name)
        if path.is_file()
    ]
    manifest: dict[str, object] = {
        "row_count": row_count,
        "frame_sha256": frame_sha256,
        "files": files,
    }
    manifest["sha256"] = json_fingerprint(manifest)
    return manifest


def runtime_manifest(
    *,
    feature_names: Sequence[str],
    calibration_path: Path,
    training_paths: Iterable[Path],
    training_row_count: int,
    training_frame_sha256: str,
) -> dict[str, object]:
    """Build the safe, serializable provenance stored with each pick file."""
    fingerprint, lock_sha256 = dependency_fingerprint()
    versions = package_versions()
    return {
        "python_version": platform.python_version(),
        "packages": versions,
        "dependency_fingerprint": fingerprint,
        "dependency_lock_sha256": lock_sha256,
        "code_commit": code_commit(),
        "code_dirty": code_is_dirty(),
        "code_source_sha256": source_fingerprint(),
        "feature_schema_sha256": json_fingerprint(list(feature_names)),
        "calibration_sha256": sha256_file(calibration_path),
        "training_data_manifest": training_manifest(
            training_paths,
            training_row_count,
            training_frame_sha256,
        ),
    }
