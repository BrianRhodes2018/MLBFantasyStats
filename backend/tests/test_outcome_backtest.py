import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from outcome_backtest import (  # noqa: E402
    WeightConfig,
    batter_total_bases,
    batting_outcome,
    cold_form_multiplier,
    pitcher_metrics_from_logs,
    weighted_score,
    woba_from_logs,
)


def test_woba_from_logs_uses_linear_weights_and_k_pct():
    result = woba_from_logs([
        {
            "at_bats": 4,
            "hits": 2,
            "doubles": 1,
            "triples": 0,
            "home_runs": 1,
            "walks": 1,
            "hit_by_pitch": 0,
            "sacrifice_flies": 0,
            "strikeouts": 1,
        }
    ])

    # BB .69 + 2B 1.25 + HR 2.02 over 5 PA
    assert result["woba"] == pytest.approx((0.69 + 1.25 + 2.02) / 5)
    assert result["k_pct"] == pytest.approx(20.0)
    assert result["pa"] == 5


def test_pitcher_metrics_from_logs_computes_prefight_rates():
    result = pitcher_metrics_from_logs([
        {
            "innings_pitched": 10.0,
            "hits_allowed": 8,
            "walks": 2,
            "hit_by_pitch": 1,
            "strikeouts": 12,
            "home_runs_allowed": 1,
            "earned_runs": 4,
        }
    ])

    assert result["innings_pitched"] == 10.0
    assert result["fip"] == pytest.approx(((13 * 1) + (3 * 3) - (2 * 12)) / 10 + 3.15)
    assert result["whip"] == pytest.approx(1.0)
    assert result["hr_per_9"] == pytest.approx(0.9)
    assert result["k_bb_pct"] == pytest.approx((12 - 2) / 41 * 100)


def test_batting_outcome_total_bases_and_flags():
    stats = {
        "atBats": 4,
        "hits": 3,
        "doubles": 1,
        "triples": 1,
        "homeRuns": 1,
        "baseOnBalls": 0,
        "strikeOuts": 1,
        "rbi": 3,
        "runs": 2,
    }

    assert batter_total_bases(stats) == 9
    outcome = batting_outcome(stats)
    assert outcome["hit"] is True
    assert outcome["tb_2_plus"] is True
    assert outcome["hr"] is True
    assert outcome["bust"] is False


def test_weighted_score_matches_config_weights_and_multipliers():
    config = WeightConfig("test", platoon=0.25, pitcher=0.25, form=0.35, bvp=0.15, note="")
    score = weighted_score(
        config,
        signal_values={
            "platoon": 1.0,
            "pitcher_vulnerability": 0.6,
            "recent_form": 0.7,
            "bvp": 0.5,
        },
        park_multiplier=1.04,
        cold_multiplier=1.0,
    )

    assert score == pytest.approx(round((0.25 + 0.15 + 0.245 + 0.075) * 1.04 * 100, 1))


def test_cold_form_multiplier_thresholds():
    assert cold_form_multiplier(None) == 1.0
    assert cold_form_multiplier(0.9) == 1.0
    assert cold_form_multiplier(0.8) == 0.5
    assert cold_form_multiplier(0.7) == 0.3
