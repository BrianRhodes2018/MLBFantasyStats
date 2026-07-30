"""Candidate-specific calibration helpers for Hit Picks V3."""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss


GRID_POINTS = 201
CLIP_EPSILON = 1e-6
SIGMOID_RECIPE = {
    "C": 1.0,
    "solver": "lbfgs",
    "random_state": 7,
}


def _logit(probabilities: np.ndarray) -> np.ndarray:
    clipped = np.clip(
        np.asarray(probabilities, dtype=np.float64),
        CLIP_EPSILON,
        1.0 - CLIP_EPSILON,
    )
    return np.log(clipped / (1.0 - clipped)).reshape(-1, 1)


def fit_lookup(
    method: str,
    raw_probabilities: np.ndarray,
    outcomes: np.ndarray,
) -> dict[str, Any]:
    """Fit a monotone calibrator and serialize it as a portable lookup."""
    raw = np.asarray(raw_probabilities, dtype=np.float64)
    y_true = np.asarray(outcomes, dtype=np.int8)
    x = np.linspace(0.0, 1.0, GRID_POINTS)
    if method == "uncalibrated":
        y = x.copy()
    elif method == "isotonic":
        estimator = IsotonicRegression(
            out_of_bounds="clip",
            increasing=True,
        )
        estimator.fit(raw, y_true)
        y = estimator.predict(x)
    elif method == "sigmoid_platt":
        estimator = LogisticRegression(**SIGMOID_RECIPE)
        estimator.fit(_logit(raw), y_true)
        y = estimator.predict_proba(_logit(x))[:, 1]
    else:
        raise ValueError(f"Unknown V3 calibration method: {method}.")
    # Numerical noise may not reverse an order-preserving calibration.
    y = np.maximum.accumulate(np.clip(y, 0.0, 1.0))
    return {
        "method": method,
        "x": [round(float(value), 8) for value in x],
        "y": [round(float(value), 8) for value in y],
    }


def apply_lookup(
    raw_probabilities: np.ndarray,
    lookup: dict[str, Any],
) -> np.ndarray:
    return np.interp(
        np.asarray(raw_probabilities, dtype=np.float64),
        lookup["x"],
        lookup["y"],
    )


def calibration_metrics(
    outcomes: np.ndarray,
    probabilities: np.ndarray,
) -> dict[str, float]:
    y_true = np.asarray(outcomes, dtype=np.int8)
    scores = np.asarray(probabilities, dtype=np.float64)
    return {
        "brier": round(float(brier_score_loss(y_true, scores)), 6),
        "log_loss": round(float(log_loss(y_true, scores)), 6),
    }


def select_method(
    *,
    fit_raw: np.ndarray,
    fit_outcomes: np.ndarray,
    validation_raw: np.ndarray,
    validation_outcomes: np.ndarray,
    maximum_brier_increase_vs_raw: float = 0.0,
) -> tuple[str, dict[str, Any]]:
    """Select calibration on development validation data only."""
    results: dict[str, Any] = {}
    raw_metrics = calibration_metrics(validation_outcomes, validation_raw)
    for method in ("uncalibrated", "isotonic", "sigmoid_platt"):
        lookup = fit_lookup(method, fit_raw, fit_outcomes)
        calibrated = apply_lookup(validation_raw, lookup)
        metrics = calibration_metrics(validation_outcomes, calibrated)
        results[method] = {
            **metrics,
            "brier_delta_vs_raw": round(
                metrics["brier"] - raw_metrics["brier"],
                6,
            ),
            "eligible": (
                metrics["brier"]
                <= raw_metrics["brier"] + maximum_brier_increase_vs_raw
            ),
        }
    eligible = [
        (entry["brier"], entry["log_loss"], method)
        for method, entry in results.items()
        if entry["eligible"]
    ]
    if not eligible:
        return "uncalibrated", results
    return min(eligible)[2], results
