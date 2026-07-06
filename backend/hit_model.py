"""Hit probability helpers for a free-data 1+ hit candidate model."""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional


LEAGUE_HIT_PER_PA = 0.225

# Season-wide free-data hit model from the 2026-03-25 through 2026-06-10
# outcome grid search. BvP remains zero until we hydrate a reliable sample.
HIT_SCORE_WEIGHTS = {
    "form": 0.44,
    "pitcher": 0.34,
    "platoon": 0.19,
    "park": 0.03,
    "bvp": 0.00,
}

EXPECTED_PA_BY_ORDER = {
    1: 4.65,
    2: 4.55,
    3: 4.45,
    4: 4.32,
    5: 4.18,
    6: 4.04,
    7: 3.90,
    8: 3.78,
    9: 3.66,
}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def plate_appearances_from_batter_row(row: Mapping[str, Any]) -> int:
    ab = int(row.get("at_bats") or 0)
    walks = int(row.get("walks") or 0)
    hbp = int(row.get("hit_by_pitch") or 0)
    sf = int(row.get("sacrifice_flies") or 0)
    return ab + walks + hbp + sf


def hit_rate_per_pa_from_batter_row(row: Mapping[str, Any]) -> Optional[float]:
    pa = plate_appearances_from_batter_row(row)
    if pa <= 0:
        return None
    return (row.get("hits") or 0) / pa


def expected_pa(
    batting_order: Optional[int],
    *,
    lineup_source: Optional[str] = None,
    lineup_confidence: Optional[float] = None,
) -> float:
    """Estimate opportunities from lineup slot, with a small projection haircut."""
    try:
        slot = int(batting_order or 0)
    except (TypeError, ValueError):
        slot = 0
    base = EXPECTED_PA_BY_ORDER.get(slot, 4.05)
    if lineup_source == "projected":
        confidence = clamp(float(lineup_confidence) if lineup_confidence is not None else 0.5, 0.0, 1.0)
        # Keep the haircut modest: projected players are still likely starters,
        # but there is a nonzero scratch/rest risk before lineups confirm.
        base *= 0.92 + (0.08 * confidence)
    return round(base, 2)


def probability_at_least_one_hit(per_pa_hit_probability: float, expected_plate_appearances: float) -> float:
    per_pa = clamp(per_pa_hit_probability, 0.0, 1.0)
    pa = max(0.0, expected_plate_appearances)
    return 1.0 - math.pow(1.0 - per_pa, pa)


def park_hit_value(park_runs_factor: Optional[int]) -> float:
    """Neutral park maps to 0.5; hitter-friendly parks move toward 1.0."""
    if park_runs_factor is None:
        return 0.5
    return clamp(0.5 + ((park_runs_factor - 100.0) / 34.0), 0.0, 1.0)


def hit_score_from_signals(
    *,
    form: float,
    pitcher: float,
    platoon: float,
    park: float,
    bvp: float = 0.5,
    weights: Mapping[str, float] = HIT_SCORE_WEIGHTS,
) -> float:
    score = (
        weights["form"] * form
        + weights["pitcher"] * pitcher
        + weights["platoon"] * platoon
        + weights["park"] * park
        + weights["bvp"] * bvp
    )
    return round(clamp(score, 0.0, 1.0) * 100.0, 1)


def per_pa_hit_probability(
    *,
    season_hit_rate_per_pa: Optional[float],
    rolling_hit_rate_per_pa: Optional[float],
    hit_score: float,
    rolling_k_pct: Optional[float],
) -> float:
    """Estimate per-PA hit probability from season baseline plus hit-score context."""
    baseline = season_hit_rate_per_pa if season_hit_rate_per_pa is not None else LEAGUE_HIT_PER_PA
    baseline = clamp(baseline, 0.12, 0.34)

    # The score is a context index, not a calibrated probability. Use it as an
    # adjustment around the hitter's own hit/PA baseline.
    score_adj = ((hit_score / 100.0) - 0.5) * 0.055

    form_adj = 0.0
    if rolling_hit_rate_per_pa is not None:
        form_adj = clamp(rolling_hit_rate_per_pa - baseline, -0.035, 0.035) * 0.45

    k_adj = 0.0
    if rolling_k_pct is not None:
        if rolling_k_pct >= 31.0:
            k_adj = -0.020
        elif rolling_k_pct >= 27.0:
            k_adj = -0.012
        elif rolling_k_pct <= 17.0:
            k_adj = 0.008

    return round(clamp(baseline + score_adj + form_adj + k_adj, 0.10, 0.36), 4)


def score_hit_candidate(
    *,
    batting_order: Optional[int],
    lineup_source: Optional[str],
    lineup_confidence: Optional[float],
    season_hit_rate_per_pa: Optional[float],
    rolling_hit_rate_per_pa: Optional[float],
    rolling_k_pct: Optional[float],
    form_signal: float,
    pitcher_signal: float,
    platoon_signal: float,
    park_runs_factor: Optional[int],
    bvp_signal: float = 0.5,
) -> dict[str, Any]:
    park_signal = park_hit_value(park_runs_factor)
    score = hit_score_from_signals(
        form=form_signal,
        pitcher=pitcher_signal,
        platoon=platoon_signal,
        park=park_signal,
        bvp=bvp_signal,
    )
    exp_pa = expected_pa(
        batting_order,
        lineup_source=lineup_source,
        lineup_confidence=lineup_confidence,
    )
    per_pa = per_pa_hit_probability(
        season_hit_rate_per_pa=season_hit_rate_per_pa,
        rolling_hit_rate_per_pa=rolling_hit_rate_per_pa,
        hit_score=score,
        rolling_k_pct=rolling_k_pct,
    )
    hit_probability = probability_at_least_one_hit(per_pa, exp_pa)

    reasons = []
    risks = []
    if batting_order and batting_order <= 5:
        reasons.append("top-half lineup slot")
    elif batting_order and batting_order >= 8:
        risks.append("lower lineup slot")
    if form_signal >= 0.60:
        reasons.append("short-window form")
    elif form_signal < 0.35:
        risks.append("weak short-window form")
    if pitcher_signal >= 0.45:
        reasons.append("pitcher allows contact")
    if platoon_signal >= 0.90:
        reasons.append("platoon edge")
    if rolling_k_pct is not None and rolling_k_pct >= 27.0:
        risks.append("elevated recent K%")
    if lineup_source == "projected":
        risks.append("projected lineup")

    return {
        "score": score,
        "hit_confidence": round(score / 100.0, 4),
        "hit_probability": round(hit_probability, 4),
        "per_pa_hit_probability": per_pa,
        "expected_pa": exp_pa,
        "weights": dict(HIT_SCORE_WEIGHTS),
        "components": {
            "form": round(form_signal, 3),
            "pitcher": round(pitcher_signal, 3),
            "platoon": round(platoon_signal, 3),
            "park": round(park_signal, 3),
            "bvp": round(bvp_signal, 3),
        },
        "reasons": reasons[:4],
        "risks": risks[:4],
        "model_version": "free_hit_v1_2026_06",
    }
