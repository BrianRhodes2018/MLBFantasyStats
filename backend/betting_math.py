"""Betting probability and edge helpers."""


def american_odds_to_implied_probability(odds: int) -> float:
    """Convert American odds into implied probability."""
    if odds == 0:
        raise ValueError("American odds cannot be 0")
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)


def remove_vig(two_way_probabilities: tuple[float, float]) -> tuple[float, float]:
    """Normalize two implied probabilities so they sum to 1."""
    over_probability, under_probability = two_way_probabilities
    total = over_probability + under_probability
    if total <= 0:
        raise ValueError("Market probabilities must sum to a positive value")
    return over_probability / total, under_probability / total


def calculate_prop_edge(
    model_over_probability: float,
    over_odds: int,
    under_odds: int,
) -> dict:
    """Calculate no-vig market probability and edge for a two-way prop."""
    if not 0 <= model_over_probability <= 1:
        raise ValueError("model_over_probability must be between 0 and 1")

    market_over = american_odds_to_implied_probability(over_odds)
    market_under = american_odds_to_implied_probability(under_odds)
    no_vig_over, no_vig_under = remove_vig((market_over, market_under))

    model_under_probability = 1 - model_over_probability
    over_edge = model_over_probability - no_vig_over
    under_edge = model_under_probability - no_vig_under
    recommended_side = "over" if over_edge >= under_edge else "under"

    return {
        "model_over_probability": round(model_over_probability, 6),
        "model_under_probability": round(model_under_probability, 6),
        "market_over_probability": round(market_over, 6),
        "market_under_probability": round(market_under, 6),
        "no_vig_over_probability": round(no_vig_over, 6),
        "no_vig_under_probability": round(no_vig_under, 6),
        "over_edge": round(over_edge, 6),
        "under_edge": round(under_edge, 6),
        "recommended_side": recommended_side,
        "recommended_edge": round(over_edge if recommended_side == "over" else under_edge, 6),
    }
