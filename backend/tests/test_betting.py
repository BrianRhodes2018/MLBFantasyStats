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
    # Rolling 1.500 OPS vs season 1.000 -> ratio 1.5 -> max
    value, fired, _ = score_recent_form(1.500, 1.000)
    assert value == 1.0
    assert fired is True


def test_recent_form_ice_cold_zero():
    # Rolling .300 OPS vs season .800 -> ratio 0.375 -> floor
    value, fired, _ = score_recent_form(0.300, 0.800)
    assert value == 0.0
    assert fired is False


def test_recent_form_at_season_average_neutral():
    # Rolling = season -> neutral 0.444... not exactly 0.5 due to formula
    # (ratio 1.0 -> (1.0 - 0.6) / 0.9 = 0.444)
    value, _, _ = score_recent_form(0.800, 0.800)
    assert abs(value - 0.444) < 0.01


def test_recent_form_fires_when_clearly_hot():
    # Avoiding the exact 1.10 threshold here because 0.880 / 0.800 is
    # 1.0999...9 in float, which would test the boundary unreliably.
    # 1.15x is unambiguously hot.
    value, fired, _ = score_recent_form(0.920, 0.800)
    assert fired is True
    assert value > 0.5


def test_recent_form_missing_data_neutral():
    value, fired, _ = score_recent_form(None, 0.800)
    assert value == 0.5
    assert fired is False


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
