"""Regression tests for locked-final report helpers."""

from __future__ import annotations

import polars as pl

from scripts.evaluate_v3_locked_final import _slice_metrics


def test_slice_metrics_accepts_a_single_outcome_class():
    frame = pl.DataFrame({
        "coverage": ["fallback", "fallback"],
        "got_hit": [0, 0],
        "probability": [0.4, 0.5],
    })
    rows = _slice_metrics(frame, "coverage")
    assert rows[0]["rows"] == 2
    assert rows[0]["actual_hit_rate"] == 0.0
    assert rows[0]["log_loss"] > 0
