"""
test_betting.py - Unit tests for backend/betting.py scoring functions

These tests pin down the math behind the betting recommendations. Every
weight tweak in Phase 3 should add or update tests here so the audit
loop has a clear before/after diff.

Run from the repo root with:
    pytest backend/tests/test_betting.py -v
"""

import sys
import os

# Allow `import betting` from the backend directory regardless of how pytest
# is invoked (from repo root, from backend/, etc.).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from betting import (
    score_platoon,
    score_pitcher_vulnerability,
    score_recent_form,
    score_bvp,
    park_factor_multiplier,
    compute_composite_score,
    BVP_MIN_PA,
)


# ---------------------------------------------------------------------------
# PLATOON
# ---------------------------------------------------------------------------

def test_platoon_lhh_vs_rhp_is_full_credit():
    value, fired, _ = score_platoon("L", "R")
    assert value == 1.0
    assert fired is True


def test_platoon_rhh_vs_lhp_is_full_credit():
    value, fired, _ = score_platoon("R", "L")
    assert value == 1.0
    assert fired is True


def test_platoon_same_handed_is_zero():
    value, fired, _ = score_platoon("R", "R")
    assert value == 0.0
    assert fired is False


def test_platoon_switch_hitter_always_wins():
    # Switch hitters bat from whichever side the pitcher disadvantages.
    value, fired, _ = score_platoon("S", "R")
    assert value == 1.0
    assert fired is True


def test_platoon_missing_data_neutral_no_fire():
    value, fired, _ = score_platoon(None, "R")
    assert value == 0.5
    assert fired is False


# ---------------------------------------------------------------------------
# PITCHER VULNERABILITY
# ---------------------------------------------------------------------------

def test_pitcher_vulnerability_elite_pitcher_zero():
    # FIP 2.50, WHIP 0.95, HR/9 0.5 — all elite => zero vulnerability
    value, fired, _ = score_pitcher_vulnerability(2.50, 0.95, 0.5)
    assert value == 0.0
    assert fired is False


def test_pitcher_vulnerability_terrible_pitcher_one():
    # All three at the "vulnerable" cap
    value, fired, _ = score_pitcher_vulnerability(5.50, 1.60, 1.80)
    assert value == 1.0
    assert fired is True


def test_pitcher_vulnerability_mid_threshold_fires():
    # FIP 4.50 -> 0.667, WHIP 1.30 -> 0.5, HR/9 1.20 -> 0.571
    # avg ~0.58 -> fires
    value, fired, _ = score_pitcher_vulnerability(4.50, 1.30, 1.20)
    assert 0.55 < value < 0.62
    assert fired is True


def test_pitcher_vulnerability_missing_treated_as_elite():
    # If we have no rate data, score 0 — don't punish a hitter just because
    # the data isn't there. Better to under-rate than over-rate by accident.
    value, fired, _ = score_pitcher_vulnerability(None, None, None)
    assert value == 0.0
    assert fired is False


# ---------------------------------------------------------------------------
# RECENT FORM
# ---------------------------------------------------------------------------

def test_recent_form_red_hot_full_credit():
    # Rolling .500 wOBA vs season .333 -> ratio 1.5 -> max rate score.
    # No K/Barrel gates supplied so no caps apply.
    value, fired, _, _ = score_recent_form(rolling_woba=0.500, season_woba=0.333)
    assert value == 1.0
    assert fired is True


def test_recent_form_ice_cold_zero():
    # Rolling .100 wOBA vs season .333 -> ratio 0.30 -> floor
    value, fired, _, _ = score_recent_form(rolling_woba=0.100, season_woba=0.333)
    assert value == 0.0
    assert fired is False


def test_recent_form_at_season_average_neutral():
    # Rolling = season -> neutral 0.444... not exactly 0.5 due to formula
    # (ratio 1.0 -> (1.0 - 0.6) / 0.9 = 0.444)
    value, _, _, _ = score_recent_form(rolling_woba=0.333, season_woba=0.333)
    assert abs(value - 0.444) < 0.01


def test_recent_form_fires_when_clearly_hot():
    # 1.15x rate ratio + no K-gate disqualification -> fires
    value, fired, _, _ = score_recent_form(rolling_woba=0.383, season_woba=0.333)
    assert fired is True
    assert value > 0.5


def test_recent_form_missing_data_neutral():
    value, fired, _, _ = score_recent_form(rolling_woba=None, season_woba=0.333)
    assert value == 0.5
    assert fired is False


def test_recent_form_high_k_pct_caps_score():
    # Same ratio as the "red hot" test, but with a 31% K rate. Should be
    # capped at 0.5x. 1.0 -> 0.5.
    value, fired, _, _ = score_recent_form(
        rolling_woba=0.500, season_woba=0.333, season_k_pct=31.0,
    )
    assert value == 0.5
    # Still fires per the rate, BUT the K-gate disqualifies it from firing.
    assert fired is False


def test_recent_form_elite_barrels_boosts_score():
    # Ratio gives base 0.444; +10 barrel rate over league avg should
    # bump us via the 1.2x bonus.
    value, _, _, _ = score_recent_form(
        rolling_woba=0.333, season_woba=0.333, season_barrel_pa_pct=12.0,
    )
    # 0.444 * 1.2 = 0.533
    assert value > 0.5


def test_recent_form_prefers_xwoba_when_available():
    # When both xwOBA and wOBA pairs are available, xwOBA should win.
    # Use ratios that would point in opposite directions to confirm.
    value, _, detail, _ = score_recent_form(
        rolling_woba=0.500, season_woba=0.333,    # wOBA says ratio 1.50 (max)
        rolling_xwoba=0.300, season_xwoba=0.333,  # xwOBA says ratio 0.90 (below neutral)
    )
    assert "xwOBA" in detail
    # If xwOBA logic was picked, value will be around 0.333 (ratio 0.9 -> (0.9 - 0.6)/0.9 = 0.333)
    assert value < 0.5


def test_recent_form_returns_ratio_when_data_present():
    _, _, _, ratio = score_recent_form(rolling_woba=0.400, season_woba=0.333)
    assert ratio is not None
    assert abs(ratio - (0.400 / 0.333)) < 1e-6


def test_recent_form_returns_none_ratio_when_data_missing():
    _, _, _, ratio = score_recent_form(rolling_woba=None, season_woba=0.333)
    assert ratio is None


def test_composite_does_not_mix_season_ops_with_season_woba_as_form():
    # Live candidates can have season Savant wOBA but no rolling game-log
    # value. A previous fallback paired season OPS as "rolling" against
    # season wOBA, creating fake hot-bat signals for cold hitters.
    result = compute_composite_score(
        bats="L", throws="R",
        pitcher_fip=3.68, pitcher_whip=1.06, pitcher_hr_per_9=0.68,
        rolling_woba=None, season_woba=0.264,
        rolling_ops=0.566, season_ops=0.566,
        park_runs_factor=104,
    )

    assert result["signals"]["recent_form"]["fired"] is False
    assert result["signals"]["recent_form"]["detail"] == "no rolling/season rate-stat data"
    assert "hot bat" not in result["summary"].lower()


# ---------------------------------------------------------------------------
# COLD-FORM MULTIPLIER (composite-score brake)
# ---------------------------------------------------------------------------

def test_composite_cold_form_multiplier_applied_when_clearly_cold():
    # ratio = 0.250 / 0.333 = 0.75 — right at the deep-cold threshold,
    # composite should be heavily damped (0.30x or 0.50x depending on
    # which side of the cut).
    result_cold = compute_composite_score(
        bats="L", throws="R",
        pitcher_fip=4.80, pitcher_whip=1.42, pitcher_hr_per_9=1.50,
        rolling_woba=0.250, season_woba=0.333,
        park_runs_factor=104,
    )
    # And an otherwise-identical hitter without cold-form data:
    result_neutral = compute_composite_score(
        bats="L", throws="R",
        pitcher_fip=4.80, pitcher_whip=1.42, pitcher_hr_per_9=1.50,
        rolling_woba=None, season_woba=None,  # no form data -> no penalty
        park_runs_factor=104,
    )
    # Cold version should be materially lower than the no-data baseline.
    assert result_cold["composite_score"] < result_neutral["composite_score"] * 0.7


def test_composite_no_cold_penalty_when_hitter_is_hot():
    # ratio 1.20 — clearly hot, no multiplier should apply.
    result_hot = compute_composite_score(
        bats="L", throws="R",
        pitcher_fip=4.80, pitcher_whip=1.42, pitcher_hr_per_9=1.50,
        rolling_woba=0.400, season_woba=0.333,  # ratio ~1.20
        park_runs_factor=104,
    )
    result_neutral = compute_composite_score(
        bats="L", throws="R",
        pitcher_fip=4.80, pitcher_whip=1.42, pitcher_hr_per_9=1.50,
        rolling_woba=None, season_woba=None,
        park_runs_factor=104,
    )
    # Hot hitter should score AT LEAST the no-data baseline (no penalty
    # AND benefit from positive form contribution).
    assert result_hot["composite_score"] >= result_neutral["composite_score"]


def test_composite_no_cold_penalty_when_form_data_missing():
    # No rolling data -> ratio is None -> no multiplier applies.
    # Confirms we don't penalize recent call-ups for whom we have no info.
    result = compute_composite_score(
        bats="L", throws="R",
        pitcher_fip=4.80, pitcher_whip=1.42, pitcher_hr_per_9=1.50,
        rolling_woba=None, season_woba=None,
        park_runs_factor=104,
    )
    # The signal value should be the neutral 0.5 (no data), and the
    # composite shouldn't be zeroed out.
    assert result["composite_score"] > 30


# ---------------------------------------------------------------------------
# BvP
# ---------------------------------------------------------------------------

def test_bvp_below_min_pa_returns_neutral_no_fire():
    # 5 PA with 1.500 OPS — too small a sample to trust
    value, fired, _ = score_bvp(5, 1.500)
    assert value == 0.5
    assert fired is False


def test_bvp_strong_history_fires():
    # 15 PA with .950 OPS — well above threshold
    value, fired, _ = score_bvp(15, 0.950)
    assert value > 0.8
    assert fired is True


def test_bvp_weak_history_zero():
    # 20 PA with .500 OPS — clearly owned by the pitcher
    value, fired, _ = score_bvp(20, 0.500)
    assert value == 0.0
    assert fired is False


def test_bvp_zero_pa():
    value, fired, _ = score_bvp(0, None)
    assert value == 0.5
    assert fired is False


# ---------------------------------------------------------------------------
# PARK FACTOR
# ---------------------------------------------------------------------------

def test_park_factor_neutral_no_fire():
    multiplier, fired, _ = park_factor_multiplier(100)
    assert multiplier == 1.0
    assert fired is False


def test_park_factor_coors_boost():
    multiplier, fired, _ = park_factor_multiplier(117)
    assert multiplier == 1.17
    assert fired is True


def test_park_factor_oracle_penalty():
    multiplier, fired, _ = park_factor_multiplier(93)
    assert multiplier == 0.93
    assert fired is True


def test_park_factor_missing_treated_as_neutral():
    multiplier, fired, _ = park_factor_multiplier(None)
    assert multiplier == 1.0
    assert fired is False


# ---------------------------------------------------------------------------
# COMPOSITE INTEGRATION
# ---------------------------------------------------------------------------

def test_composite_judge_at_yankee_stadium_against_vulnerable_lhp():
    # Aaron Judge (RHH) vs vulnerable LHP at Yankee Stadium, hot last 14 days,
    # career mastery of this pitcher.
    result = compute_composite_score(
        bats="R", throws="L",
        pitcher_fip=4.92, pitcher_whip=1.45, pitcher_hr_per_9=1.30,
        rolling_ops=1.150, season_ops=0.950,
        bvp_pa=15, bvp_ops=1.100,
        park_runs_factor=104,
    )
    # Expect strong score: all four signals fire, slight park boost
    assert result["composite_score"] >= 80
    assert result["signals"]["platoon"]["fired"] is True
    assert result["signals"]["pitcher_vulnerability"]["fired"] is True
    assert result["signals"]["recent_form"]["fired"] is True
    assert result["signals"]["bvp"]["fired"] is True


def test_composite_weak_matchup_low_score():
    # RHH vs RHP, dominant pitcher, hitter slumping, no BvP, neutral park
    result = compute_composite_score(
        bats="R", throws="R",
        pitcher_fip=2.85, pitcher_whip=0.95, pitcher_hr_per_9=0.6,
        rolling_ops=0.500, season_ops=0.800,
        bvp_pa=None, bvp_ops=None,
        park_runs_factor=100,
    )
    assert result["composite_score"] < 25
    # No signals should fire
    assert result["signals"]["platoon"]["fired"] is False
    assert result["signals"]["pitcher_vulnerability"]["fired"] is False
    assert result["signals"]["recent_form"]["fired"] is False
    assert result["signals"]["bvp"]["fired"] is False


def test_composite_park_factor_actually_multiplies():
    # Same matchup at Coors vs Oracle — Coors score should be higher
    base_kwargs = dict(
        bats="L", throws="R",
        pitcher_fip=4.50, pitcher_whip=1.35, pitcher_hr_per_9=1.20,
        rolling_ops=0.950, season_ops=0.850,
        bvp_pa=None, bvp_ops=None,
    )
    coors = compute_composite_score(**base_kwargs, park_runs_factor=117)
    oracle = compute_composite_score(**base_kwargs, park_runs_factor=93)
    assert coors["composite_score"] > oracle["composite_score"]
    # Specifically, Coors score should be ~117/93 of Oracle score
    ratio = coors["composite_score"] / oracle["composite_score"]
    assert 1.20 < ratio < 1.30  # 117/93 ≈ 1.258


def test_composite_summary_names_fired_signals():
    result = compute_composite_score(
        bats="L", throws="R",
        pitcher_fip=5.20, pitcher_whip=1.55, pitcher_hr_per_9=1.60,
        rolling_ops=1.100, season_ops=0.900,
        bvp_pa=12, bvp_ops=0.950,
        park_runs_factor=110,
    )
    summary = result["summary"].lower()
    # Should mention multiple signals — exact wording may evolve, but the
    # test ensures the helper isn't returning a generic blurb when signals
    # actually fired.
    assert "platoon" in summary
    assert "pitcher" in summary or "vulnerable" in summary
