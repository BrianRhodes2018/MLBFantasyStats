"""Tests for V3 candidate-specific calibration selection."""

from __future__ import annotations

import numpy as np

from hit_model.v3_calibration import (
    apply_lookup,
    fit_lookup,
    select_method,
)


def test_every_calibration_lookup_is_monotone():
    raw = np.linspace(0.1, 0.9, 40)
    outcomes = (raw > 0.55).astype(int)
    for method in ("uncalibrated", "isotonic", "sigmoid_platt"):
        lookup = fit_lookup(method, raw, outcomes)
        assert len(lookup["x"]) == 201
        calibrated = apply_lookup(np.linspace(0.0, 1.0, 1001), lookup)
        assert np.all(np.diff(calibrated) >= -1e-12)


def test_method_selection_includes_raw_as_safe_fallback():
    fit_raw = np.array([0.2, 0.3, 0.7, 0.8])
    fit_y = np.array([0, 0, 1, 1])
    validation_raw = np.array([0.1, 0.4, 0.6, 0.9])
    validation_y = np.array([0, 0, 1, 1])
    method, results = select_method(
        fit_raw=fit_raw,
        fit_outcomes=fit_y,
        validation_raw=validation_raw,
        validation_outcomes=validation_y,
    )
    assert method in results
    assert results["uncalibrated"]["eligible"] is True
