"""Fail-fast validation for the locked ML runtime.

This command is intentionally database-free and network-free. It catches native
binary incompatibilities before a scheduled run grades or publishes any picks.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import tempfile
import warnings
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from ml_environment import dependency_fingerprint, package_versions

ABI_SIGNATURES = (
    "_ARRAY_API",
    "multiarray failed to import",
    "numpy 1.x",
    "numpy.core",
    "binary incompatibility",
)

# joblib otherwise probes Windows hardware through a subprocess. Restricted
# scheduled-task accounts can reject that probe and print a long, harmless
# traceback, so use the already-available logical CPU count explicitly.
logical_cpu_count = os.cpu_count() or 1
os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(max(1, logical_cpu_count - 1)))


def validate_environment(*, require_venv: bool = True) -> dict[str, object]:
    if require_venv and sys.prefix == sys.base_prefix:
        raise RuntimeError("Python is not running inside the project virtual environment.")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        modules = {
            name: importlib.import_module(name)
            for name in ("numpy", "scipy", "pandas", "pyarrow", "polars", "sklearn")
        }

        np = modules["numpy"]
        pl = modules["polars"]
        classifier = importlib.import_module("sklearn.ensemble").HistGradientBoostingClassifier

        x = np.arange(160, dtype=float).reshape(40, 4)
        y = np.array([0, 1] * 20, dtype=int)
        probabilities = classifier(
            max_iter=5,
            min_samples_leaf=2,
            random_state=42,
        ).fit(x, y).predict_proba(x[:3])
        if probabilities.shape != (3, 2) or not np.isfinite(probabilities).all():
            raise RuntimeError("HistGradientBoostingClassifier smoke prediction is invalid.")

        frame = pl.DataFrame({"player_id": [1, 2], "hit_probability": [0.61, 0.73]})
        pandas_frame = frame.to_pandas()
        if pandas_frame.shape != (2, 2):
            raise RuntimeError("Polars-to-pandas conversion returned an unexpected shape.")

        with tempfile.TemporaryDirectory(prefix="mlb-ml-smoke-") as directory:
            parquet_path = Path(directory) / "roundtrip.parquet"
            frame.write_parquet(parquet_path)
            restored = pl.read_parquet(parquet_path)
            if not frame.equals(restored):
                raise RuntimeError("Polars Parquet round trip changed the data.")

    bad_warnings = [
        str(item.message)
        for item in caught
        if any(signature.lower() in str(item.message).lower() for signature in ABI_SIGNATURES)
    ]
    if bad_warnings:
        raise RuntimeError(f"ABI/import compatibility warning detected: {bad_warnings[0]}")

    fingerprint, lock_sha256 = dependency_fingerprint()
    return {
        "status": "ok",
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "virtual_environment": sys.prefix,
        "packages": package_versions(),
        "dependency_lock_sha256": lock_sha256,
        "dependency_fingerprint": fingerprint,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the locked MLB ML environment.")
    parser.add_argument("--json", action="store_true", help="Print a compact JSON result.")
    parser.add_argument(
        "--allow-system-python",
        action="store_true",
        help="Do not require an active virtual environment (intended for diagnostics only).",
    )
    args = parser.parse_args()

    try:
        result = validate_environment(require_venv=not args.allow_system_python)
    except Exception as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        else:
            print(f"ML environment check failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"ML environment check passed ({result['dependency_fingerprint']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
