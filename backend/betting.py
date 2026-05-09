"""
betting.py - Composite Betting-Edge Scoring
============================================

Pure scoring functions for the /betting/candidates endpoint. Each function
takes the inputs it needs and returns a (value, fired, detail) tuple:

    value   : float in [0.0, 1.0] — how strong this signal is for this hitter
    fired   : bool — whether the signal triggered (used by the UI to bold the chip)
    detail  : short human-readable string ("RHH vs LHP", "FIP 4.92, WHIP 1.45", ...)

Why "pure" matters here:
    These functions are the heart of the betting product — if the math is
    wrong or drifts, every recommendation is wrong. Keeping them as side-effect
    free Python (no DB calls, no HTTP) means we can unit-test them with
    fake inputs and pin behavior down before hitting production data.
    See backend/tests/test_betting.py for the corresponding test cases.

Composite score:
    score = WEIGHT_PLATOON   * platoon
          + WEIGHT_PITCHER   * pitcher_vulnerability
          + WEIGHT_FORM      * recent_form
          + WEIGHT_BVP       * bvp
    score *= park_factor / 100   # park multiplier
    score *= 100                  # rescale to a friendly 0–100ish badge number

Weights are intentionally first-pass guesses. Phase 3 of the betting plan
re-tunes them based on accumulated audit data. Don't treat them as gospel.
"""

from __future__ import annotations

import json
from typing import Optional


# ---------------------------------------------------------------------------
# WEIGHTS
# ---------------------------------------------------------------------------
# Sum to 1.0. Adjust per Phase 3 audit findings, not by gut feel.
WEIGHT_PLATOON = 0.30
WEIGHT_PITCHER = 0.30
WEIGHT_FORM = 0.20
WEIGHT_BVP = 0.20


# ---------------------------------------------------------------------------
# 1. PLATOON ADVANTAGE
# ---------------------------------------------------------------------------

def score_platoon(bats: Optional[str], throws: Optional[str]) -> tuple[float, bool, str]:
    """
    Hitters see opposite-handed pitchers better — almost universally true.

    Returns 1.0 for opposite-handed matchups, 0.0 for same-handed, and 1.0
    for switch hitters (they'll bat from whichever side has the platoon
    advantage). 0.5 when handedness data is missing on either side, so we
    don't unfairly punish a hitter for incomplete data.

    Inputs are MLB API codes: 'L', 'R', 'S' (switch — only for batters).
    """
    if not bats or not throws:
        return 0.5, False, "handedness data missing"

    bats = bats.upper()
    throws = throws.upper()

    # Switch hitters always get the platoon edge.
    if bats == "S":
        return 1.0, True, f"Switch hitter vs {throws}HP"

    if bats != throws:
        return 1.0, True, f"{bats}HH vs {throws}HP (opposite-handed)"

    return 0.0, False, f"{bats}HH vs {throws}HP (same-handed)"


# ---------------------------------------------------------------------------
# 2. PITCHER VULNERABILITY
# ---------------------------------------------------------------------------
# Built from FIP, WHIP, and HR/9. Each gets normalized to a [0, 1] "badness"
# score using thresholds informed by industry conventions (see comments).
# We average the three to keep one bad metric from dominating; the audit
# can later tell us whether to weight one higher.

def _normalize_fip(fip: Optional[float]) -> float:
    """FIP < 3.50 is elite; FIP > 5.00 is vulnerable. Linear in between."""
    if fip is None:
        return 0.0
    if fip <= 3.50:
        return 0.0
    if fip >= 5.00:
        return 1.0
    return (fip - 3.50) / 1.50


def _normalize_whip(whip: Optional[float]) -> float:
    """WHIP < 1.10 is elite; WHIP > 1.50 is vulnerable."""
    if whip is None:
        return 0.0
    if whip <= 1.10:
        return 0.0
    if whip >= 1.50:
        return 1.0
    return (whip - 1.10) / 0.40


def _normalize_hr9(hr_per_9: Optional[float]) -> float:
    """HR/9 < 0.80 is elite; HR/9 > 1.50 is vulnerable to the long ball."""
    if hr_per_9 is None:
        return 0.0
    if hr_per_9 <= 0.80:
        return 0.0
    if hr_per_9 >= 1.50:
        return 1.0
    return (hr_per_9 - 0.80) / 0.70


def score_pitcher_vulnerability(
    fip: Optional[float],
    whip: Optional[float],
    hr_per_9: Optional[float],
) -> tuple[float, bool, str]:
    """
    0.0 = elite/dominant pitcher, 1.0 = batting practice. Average of three
    normalized metrics so one outlier doesn't dominate.

    Fires when the average exceeds 0.40 — roughly "this pitcher has at
    least one materially below-average rate stat". Threshold is a guess;
    Phase 3 will tune it.
    """
    components = [
        _normalize_fip(fip),
        _normalize_whip(whip),
        _normalize_hr9(hr_per_9),
    ]
    score = sum(components) / len(components)
    fired = score >= 0.40

    detail_parts = []
    if fip is not None:
        detail_parts.append(f"FIP {fip:.2f}")
    if whip is not None:
        detail_parts.append(f"WHIP {whip:.2f}")
    if hr_per_9 is not None:
        detail_parts.append(f"HR/9 {hr_per_9:.2f}")
    detail = ", ".join(detail_parts) if detail_parts else "no pitcher rate stats available"

    return score, fired, detail


# ---------------------------------------------------------------------------
# 3. RECENT FORM (HOT/COLD STREAK)
# ---------------------------------------------------------------------------

def score_recent_form(
    rolling_ops: Optional[float],
    season_ops: Optional[float],
) -> tuple[float, bool, str]:
    """
    Reward hitters whose 14-day rolling OPS is meaningfully higher than
    their season OPS, penalize the opposite. Linearly interpolated between
    "rolling = 0.6 * season" (score 0) and "rolling = 1.5 * season" (score 1).
    Equal rolling and season OPS yields 0.5.

    The fired threshold is "rolling at least 1.10x season OPS" — clearly
    hot, not just bouncing around the mean.
    """
    if rolling_ops is None or season_ops is None or season_ops <= 0:
        return 0.5, False, "no rolling/season data"

    ratio = rolling_ops / season_ops

    if ratio >= 1.5:
        score = 1.0
    elif ratio <= 0.6:
        score = 0.0
    else:
        # Linear from 0.6 -> 0 to 1.5 -> 1
        score = (ratio - 0.6) / 0.9

    fired = ratio >= 1.10
    detail = f".{int(rolling_ops * 1000):03d} rolling OPS vs .{int(season_ops * 1000):03d} season ({ratio:.2f}x)"
    return score, fired, detail


# ---------------------------------------------------------------------------
# 4. BvP (BATTER vs PITCHER HISTORY)
# ---------------------------------------------------------------------------

# Plate appearances below this threshold are considered too noisy to trust.
# 10 PA gives roughly enough signal that the OPS isn't dominated by one
# random hot day. Industry rule of thumb is 25+ PA but 10 is a reasonable
# Phase 1 floor that will catch obvious cases like "this guy is 8-for-15
# with 3 HRs lifetime against this pitcher".
BVP_MIN_PA = 10


def score_bvp(career_pa: Optional[int], career_ops: Optional[float]) -> tuple[float, bool, str]:
    """
    Career hitter-vs-pitcher OPS, only when the sample is meaningful.

    < 10 PA: returns 0.5 with fired=False ("not enough data" — neutral).
    OPS >= 1.000 over the sample: 1.0
    OPS <= 0.600 over the sample: 0.0
    Linear in between.
    """
    if career_pa is None or career_pa < BVP_MIN_PA or career_ops is None:
        pa_text = career_pa if career_pa is not None else 0
        return 0.5, False, f"insufficient sample ({pa_text} PA, need {BVP_MIN_PA}+)"

    if career_ops >= 1.000:
        score = 1.0
    elif career_ops <= 0.600:
        score = 0.0
    else:
        score = (career_ops - 0.600) / 0.400

    fired = career_ops >= 0.800
    detail = f"{career_pa} PA, .{int(career_ops * 1000):03d} OPS career vs this pitcher"
    return score, fired, detail


# ---------------------------------------------------------------------------
# 5. PARK FACTOR
# ---------------------------------------------------------------------------

def park_factor_multiplier(runs_factor: Optional[int]) -> tuple[float, bool, str]:
    """
    Returns the multiplier we apply to the composite score, plus a
    fired/detail tuple in the same shape as the other signals (so the UI
    can render it as a chip).

    "Fired" means materially non-neutral (factor > 103 or < 97).
    The multiplier itself is `runs_factor / 100`, so Coors at 117 gets
    a 1.17x boost and Oracle at 94 gets a 0.94x penalty.
    """
    if runs_factor is None:
        return 1.0, False, "park factor unavailable (treated as neutral)"

    multiplier = runs_factor / 100.0
    fired = runs_factor > 103 or runs_factor < 97
    if runs_factor > 103:
        category = "hitter-friendly"
    elif runs_factor < 97:
        category = "pitcher-friendly"
    else:
        category = "neutral"
    detail = f"runs factor {runs_factor} ({category})"
    return multiplier, fired, detail


# ---------------------------------------------------------------------------
# COMPOSITE
# ---------------------------------------------------------------------------

def compute_composite_score(
    *,
    bats: Optional[str],
    throws: Optional[str],
    pitcher_fip: Optional[float],
    pitcher_whip: Optional[float],
    pitcher_hr_per_9: Optional[float],
    rolling_ops: Optional[float],
    season_ops: Optional[float],
    bvp_pa: Optional[int],
    bvp_ops: Optional[float],
    park_runs_factor: Optional[int],
) -> dict:
    """
    Run all five signals and combine into a single composite score.

    Returns a dict with:
        composite_score: float in [0, ~150] — final number we rank on
        signals: dict[name, {value, fired, detail}] for UI rendering
        summary: short human sentence summarizing why this hitter is on the list

    Higher composite_score = stronger pick. We rescale to 0-100ish (then
    park_factor stretches it slightly above) so badge numbers feel familiar
    rather than the 0–1 floats inside the math.
    """
    platoon_v, platoon_fired, platoon_d = score_platoon(bats, throws)
    pitcher_v, pitcher_fired, pitcher_d = score_pitcher_vulnerability(pitcher_fip, pitcher_whip, pitcher_hr_per_9)
    form_v, form_fired, form_d = score_recent_form(rolling_ops, season_ops)
    bvp_v, bvp_fired, bvp_d = score_bvp(bvp_pa, bvp_ops)
    park_mult, park_fired, park_d = park_factor_multiplier(park_runs_factor)

    raw = (
        WEIGHT_PLATOON * platoon_v
        + WEIGHT_PITCHER * pitcher_v
        + WEIGHT_FORM * form_v
        + WEIGHT_BVP * bvp_v
    )
    composite = round(raw * park_mult * 100, 1)

    # Build a one-line summary that names the strongest signals. Avoids
    # generic "great matchup" language so the user sees *why* this row.
    fired_signals = []
    if platoon_fired:
        fired_signals.append("platoon edge")
    if pitcher_fired:
        fired_signals.append("vulnerable pitcher")
    if form_fired:
        fired_signals.append("hot bat")
    if bvp_fired:
        fired_signals.append("strong BvP history")
    if park_fired and park_runs_factor and park_runs_factor > 103:
        fired_signals.append("hitter-friendly park")

    if fired_signals:
        summary = "; ".join(fired_signals).capitalize() + "."
    else:
        summary = "Composite score driven by partial signals; review the breakdown for nuance."

    return {
        "composite_score": composite,
        "signals": {
            "platoon": {"value": round(platoon_v, 3), "fired": platoon_fired, "detail": platoon_d},
            "pitcher_vulnerability": {"value": round(pitcher_v, 3), "fired": pitcher_fired, "detail": pitcher_d},
            "recent_form": {"value": round(form_v, 3), "fired": form_fired, "detail": form_d},
            "bvp": {"value": round(bvp_v, 3), "fired": bvp_fired, "detail": bvp_d},
            "park_factor": {"value": round(park_mult, 3), "fired": park_fired, "detail": park_d},
        },
        "summary": summary,
    }


def signals_to_json(signals: dict) -> str:
    """
    Serialize the per-signal breakdown for storage in
    bet_suggestions.signals_json. Stable dict ordering means audits diffing
    historical rows aren't fooled by key reordering.
    """
    return json.dumps(signals, sort_keys=True)
