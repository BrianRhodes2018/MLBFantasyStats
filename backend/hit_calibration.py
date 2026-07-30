"""
hit_calibration.py - Apply the isotonic probability calibration curve.

The hit model ranks well but overstates its strongest probabilities (a
raw "75%" historically comes true ~68% of the time). Calibration fixes
the NUMBERS without touching the RANKING: an isotonic (order-preserving)
curve, fitted on out-of-sample predictions vs real outcomes, translates
each raw probability into what that raw value has actually delivered.

The curve is fitted offline by `train_hit_model.py --fit-calibrator`
and stored as a small JSON file (a 201-point lookup table) committed to
the repo — reviewable in PRs like any other model change. This module
is the tiny runtime side: load the file, translate probabilities by
linear interpolation.

Because the curve never decreases, calibrated probabilities keep the
exact same order as raw ones — same picks, same top-10, only honest
numbers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ml_environment import dependency_fingerprint, json_fingerprint

BACKEND_DIR = Path(__file__).resolve().parent
CALIBRATION_PATH = BACKEND_DIR / "calibration" / "hit_gbm_v2_isotonic.json"


class CalibrationCompatibilityError(RuntimeError):
    """The curve was created for a different model/runtime contract."""


def load_calibration(
    path: Path = CALIBRATION_PATH,
    *,
    expected_model_recipe_sha256: Optional[str] = None,
    expected_feature_schema_sha256: Optional[str] = None,
    expected_dependency_fingerprint: Optional[str] = None,
    allow_incompatible: bool = False,
) -> Optional[dict[str, Any]]:
    """Load a curve only when it matches the exact active model bundle.

    `allow_incompatible` exists for deliberate offline investigation. The
    scheduled prediction job never enables it.
    """
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not payload.get("x") or not payload.get("y"):
        raise CalibrationCompatibilityError("Calibration curve is malformed.")

    if expected_model_recipe_sha256 is None or expected_feature_schema_sha256 is None:
        # Lazy import avoids a train_hit_model -> hit_calibration import cycle.
        from train_hit_model import FEATURES, model_recipe_fingerprint

        expected_model_recipe_sha256 = (
            expected_model_recipe_sha256 or model_recipe_fingerprint()
        )
        expected_feature_schema_sha256 = (
            expected_feature_schema_sha256 or json_fingerprint(FEATURES)
        )
    if expected_dependency_fingerprint is None:
        expected_dependency_fingerprint, _ = dependency_fingerprint()

    expected = {
        "base_model_recipe_sha256": expected_model_recipe_sha256,
        "feature_schema_sha256": expected_feature_schema_sha256,
        "dependency_fingerprint": expected_dependency_fingerprint,
    }
    mismatches = [
        key
        for key, value in expected.items()
        if not payload.get(key) or payload.get(key) != value
    ]
    if mismatches and not allow_incompatible:
        raise CalibrationCompatibilityError(
            "Calibration bundle is incompatible with the active model: "
            + ", ".join(mismatches)
            + ". Refit the calibrator before publishing."
        )
    return payload


def apply_calibration(probs: np.ndarray, calibration: dict[str, Any]) -> np.ndarray:
    """Translate raw model probabilities through the isotonic curve.

    np.interp clamps outside the grid, and the curve is monotonically
    non-decreasing, so output order always matches input order.
    """
    return np.interp(probs, calibration["x"], calibration["y"])
