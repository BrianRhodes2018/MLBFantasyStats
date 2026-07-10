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

BACKEND_DIR = Path(__file__).resolve().parent
CALIBRATION_PATH = BACKEND_DIR / "calibration" / "hit_gbm_v2_isotonic.json"


def load_calibration(path: Path = CALIBRATION_PATH) -> Optional[dict[str, Any]]:
    """The stored curve, or None when no calibration file exists."""
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not payload.get("x") or not payload.get("y"):
        return None
    return payload


def apply_calibration(probs: np.ndarray, calibration: dict[str, Any]) -> np.ndarray:
    """Translate raw model probabilities through the isotonic curve.

    np.interp clamps outside the grid, and the curve is monotonically
    non-decreasing, so output order always matches input order.
    """
    return np.interp(probs, calibration["x"], calibration["y"])
