import pytest

from betting_math import (
    american_odds_to_implied_probability,
    calculate_prop_edge,
    remove_vig,
)


def test_american_odds_to_implied_probability():
    assert american_odds_to_implied_probability(120) == pytest.approx(0.454545, abs=0.000001)
    assert american_odds_to_implied_probability(-150) == pytest.approx(0.6)


def test_american_odds_rejects_zero():
    with pytest.raises(ValueError):
        american_odds_to_implied_probability(0)


def test_remove_vig_normalizes_market_probabilities():
    no_vig_over, no_vig_under = remove_vig((0.454545, 0.6))

    assert no_vig_over + no_vig_under == pytest.approx(1.0)
    assert no_vig_over == pytest.approx(0.431034, abs=0.000001)


def test_calculate_prop_edge_compares_model_to_no_vig_market():
    edge = calculate_prop_edge(
        model_over_probability=0.58,
        over_odds=120,
        under_odds=-150,
    )

    assert edge["recommended_side"] == "over"
    assert edge["over_edge"] == pytest.approx(0.148966, abs=0.000001)
