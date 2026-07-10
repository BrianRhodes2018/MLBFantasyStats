"""Tests for the isotonic calibration curve and its runtime application."""

import numpy as np

from hit_calibration import CALIBRATION_PATH, apply_calibration, load_calibration


class TestCommittedCurve:
    def test_curve_file_is_present_and_sane(self):
        calibration = load_calibration()
        assert calibration is not None, (
            f"{CALIBRATION_PATH} missing — refit with "
            "train_hit_model.py --fit-calibrator"
        )
        x, y = calibration["x"], calibration["y"]
        assert len(x) == len(y) == 201
        assert all(0.0 <= v <= 1.0 for v in y)
        # x is the fixed grid; y must be monotonically non-decreasing —
        # the property that guarantees calibration never reorders picks.
        assert all(b >= a for a, b in zip(x, x[1:]))
        assert all(b >= a - 1e-9 for a, b in zip(y, y[1:]))

    def test_curve_improved_brier(self):
        calibration = load_calibration()
        assert calibration["brier_calibrated"] <= calibration["brier_raw"]


class TestApplyCalibration:
    def test_identity_curve_changes_nothing(self):
        grid = np.linspace(0, 1, 201)
        identity = {"x": grid.tolist(), "y": grid.tolist()}
        probs = np.array([0.1, 0.55, 0.9])
        assert np.allclose(apply_calibration(probs, identity), probs)

    def test_order_is_always_preserved(self):
        calibration = load_calibration()
        rng = np.random.default_rng(7)
        probs = rng.uniform(0.05, 0.95, size=500)
        calibrated = apply_calibration(probs, calibration)
        original_order = np.argsort(probs, kind="stable")
        # Sorting by raw and by calibrated probability must agree
        # (ties in the calibrated values are allowed — flat curve
        # segments — so compare calibrated values, not indices).
        assert np.all(np.diff(calibrated[original_order]) >= -1e-12)

    def test_out_of_range_inputs_are_clamped(self):
        calibration = {"x": [0.0, 0.5, 1.0], "y": [0.2, 0.5, 0.8]}
        out = apply_calibration(np.array([-0.5, 1.5]), calibration)
        assert out[0] == 0.2 and out[1] == 0.8
