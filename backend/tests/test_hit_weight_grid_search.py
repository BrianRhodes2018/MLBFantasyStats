import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hit_weight_grid_search import (  # noqa: E402
    HitWeightConfig,
    candidate_features,
    generate_weight_configs,
    hit_score,
    park_hit_value,
    split_days,
)


def test_generate_weight_configs_medium_respects_sum_and_ranges():
    configs = generate_weight_configs("medium")

    assert configs
    assert all(
        cfg.form + cfg.pitcher + cfg.platoon + cfg.park + cfg.bvp == 100
        for cfg in configs
    )
    assert all(30 <= cfg.form <= 60 for cfg in configs)
    assert all(15 <= cfg.pitcher <= 40 for cfg in configs)
    assert all(15 <= cfg.platoon <= 40 for cfg in configs)
    assert all(0 <= cfg.park <= 8 for cfg in configs)
    assert all(0 <= cfg.bvp <= 5 for cfg in configs)


@pytest.mark.parametrize(
    ("multiplier", "expected"),
    [
        (1.00, 0.5),
        (1.17, 1.0),
        (0.83, 0.0),
        (None, 0.5),
    ],
)
def test_park_hit_value_is_neutral_centered(multiplier, expected):
    assert park_hit_value(multiplier) == pytest.approx(expected)


def test_candidate_features_uses_hit_score_order():
    candidate = {
        "signal_values": {
            "recent_form": 0.7,
            "pitcher_vulnerability": 0.6,
            "platoon": 1.0,
            "bvp": 0.5,
        },
        "park_multiplier": 1.0,
    }

    assert candidate_features(candidate) == pytest.approx((0.7, 0.6, 1.0, 0.5, 0.5))


def test_hit_score_uses_integer_weights():
    config = HitWeightConfig(form=40, pitcher=25, platoon=25, park=5, bvp=5)

    assert hit_score(config, (0.7, 0.6, 1.0, 0.5, 0.5)) == pytest.approx(
        40 * 0.7 + 25 * 0.6 + 25 * 1.0 + 5 * 0.5 + 5 * 0.5
    )


def test_split_days_uses_last_n_as_holdout():
    train, holdout = split_days(["2026-06-03", "2026-06-01", "2026-06-02"], 1)

    assert train == {"2026-06-01", "2026-06-02"}
    assert holdout == {"2026-06-03"}
