import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hit_model import (  # noqa: E402
    expected_pa,
    hit_rate_per_pa_from_batter_row,
    hit_score_from_signals,
    park_hit_value,
    per_pa_hit_probability,
    probability_at_least_one_hit,
    score_hit_candidate,
)


def test_hit_rate_per_pa_from_batter_row_uses_pa_denominator():
    row = {
        "hits": 2,
        "at_bats": 4,
        "walks": 1,
        "hit_by_pitch": 0,
        "sacrifice_flies": 1,
    }

    assert hit_rate_per_pa_from_batter_row(row) == pytest.approx(2 / 6)


def test_expected_pa_uses_lineup_slot_and_projection_haircut():
    assert expected_pa(1) > expected_pa(8)
    assert expected_pa(2, lineup_source="projected", lineup_confidence=0.5) < expected_pa(2)


def test_probability_at_least_one_hit_compounds_per_pa_probability():
    assert probability_at_least_one_hit(0.25, 4.0) == pytest.approx(1 - (0.75 ** 4))


def test_hit_score_from_signals_weights_context():
    score = hit_score_from_signals(
        form=0.8,
        pitcher=0.6,
        platoon=1.0,
        park=0.5,
        bvp=0.5,
    )

    assert score == pytest.approx((0.44 * 0.8 + 0.34 * 0.6 + 0.19 * 1.0 + 0.03 * 0.5) * 100)


@pytest.mark.parametrize(
    ("runs_factor", "expected"),
    [
        (100, 0.5),
        (117, 1.0),
        (83, 0.0),
        (None, 0.5),
    ],
)
def test_park_hit_value_is_neutral_centered(runs_factor, expected):
    assert park_hit_value(runs_factor) == pytest.approx(expected)


def test_per_pa_hit_probability_blends_baseline_context_and_k_risk():
    strong = per_pa_hit_probability(
        season_hit_rate_per_pa=0.23,
        rolling_hit_rate_per_pa=0.30,
        hit_score=80.0,
        rolling_k_pct=16.0,
    )
    risky = per_pa_hit_probability(
        season_hit_rate_per_pa=0.23,
        rolling_hit_rate_per_pa=0.30,
        hit_score=80.0,
        rolling_k_pct=32.0,
    )

    assert strong > risky


def test_score_hit_candidate_returns_probability_and_reasons():
    result = score_hit_candidate(
        batting_order=2,
        lineup_source="confirmed",
        lineup_confidence=None,
        season_hit_rate_per_pa=0.23,
        rolling_hit_rate_per_pa=0.29,
        rolling_k_pct=18.0,
        form_signal=0.8,
        pitcher_signal=0.6,
        platoon_signal=1.0,
        park_runs_factor=104,
    )

    assert result["score"] > 70
    assert result["hit_confidence"] == pytest.approx(result["score"] / 100)
    assert 0 < result["hit_probability"] < 1
    assert result["expected_pa"] > 4.0
    assert "top-half lineup slot" in result["reasons"]
