"""Unit tests for the pure feature-extraction helpers in build_hit_dataset.py."""

import pytest

from build_hit_dataset import (
    VsHandHistory,
    batter_window_stats,
    batting_line_from_boxscore,
    log_row_pa,
    pitcher_agg,
    pitching_line_from_boxscore,
    platoon_advantage,
)


def batter_row(**overrides):
    row = {
        "at_bats": 4,
        "hits": 1,
        "doubles": 0,
        "triples": 0,
        "home_runs": 0,
        "walks": 0,
        "hit_by_pitch": 0,
        "sacrifice_flies": 0,
        "strikeouts": 1,
    }
    row.update(overrides)
    return row


class TestLogRowPa:
    def test_sums_ab_bb_hbp_sf(self):
        row = batter_row(at_bats=3, walks=1, hit_by_pitch=1, sacrifice_flies=1)
        assert log_row_pa(row) == 6

    def test_handles_missing_values(self):
        assert log_row_pa({}) == 0


class TestBatterWindowStats:
    def test_empty_rows_return_none_rates(self):
        stats = batter_window_stats([])
        assert stats["pa"] == 0
        assert stats["hit_per_pa"] is None
        assert stats["k_pct"] is None
        assert stats["contact_rate"] is None
        assert stats["woba"] is None

    def test_basic_rates(self):
        # Two games: 8 AB, 3 H, 2 K, no walks -> PA = 8.
        rows = [
            batter_row(hits=2, strikeouts=1),
            batter_row(hits=1, strikeouts=1),
        ]
        stats = batter_window_stats(rows)
        assert stats["pa"] == 8
        assert stats["hit_per_pa"] == pytest.approx(3 / 8)
        assert stats["k_pct"] == pytest.approx(25.0)
        assert stats["contact_rate"] == pytest.approx(6 / 8)

    def test_woba_single_only(self):
        # 4 AB, 1 single: wOBA = 0.88 / 4.
        stats = batter_window_stats([batter_row()])
        assert stats["woba"] == pytest.approx(0.88 / 4)

    def test_woba_counts_extra_base_hits(self):
        # A HR is worth more than a single.
        hr_game = batter_window_stats([batter_row(home_runs=1)])
        single_game = batter_window_stats([batter_row()])
        assert hr_game["woba"] > single_game["woba"]


class TestPitcherAgg:
    def test_zero_ip_returns_none_rates(self):
        stats = pitcher_agg([])
        assert stats["ip"] == 0.0
        assert stats["h_per_9"] is None
        assert stats["whip"] is None

    def test_basic_rates(self):
        rows = [{
            "innings_pitched": 9.0,
            "hits_allowed": 9,
            "earned_runs": 3,
            "walks": 3,
            "strikeouts": 9,
            "home_runs_allowed": 1,
            "hit_by_pitch": 0,
        }]
        stats = pitcher_agg(rows)
        assert stats["h_per_9"] == pytest.approx(9.0)
        assert stats["whip"] == pytest.approx(12 / 9)
        assert stats["hr_per_9"] == pytest.approx(1.0)
        # PA est = 27 + 9 + 3 + 0 = 39
        assert stats["k_pct"] == pytest.approx(9 / 39 * 100.0)
        assert stats["k_bb_pct"] == pytest.approx(6 / 39 * 100.0)
        # FIP = (13*1 + 3*3 - 2*9) / 9 + 3.15
        assert stats["fip"] == pytest.approx((13 + 9 - 18) / 9 + 3.15)

    def test_prefers_true_batters_faced_over_estimate(self):
        rows = [{
            "innings_pitched": 9.0,
            "hits_allowed": 9,
            "walks": 3,
            "strikeouts": 9,
            "hit_by_pitch": 0,
            "batters_faced": 40,  # estimate would say 39
        }]
        stats = pitcher_agg(rows)
        assert stats["k_pct"] == pytest.approx(9 / 40 * 100.0)


class TestBoxscoreConversion:
    def test_batting_line_maps_keys(self):
        line = batting_line_from_boxscore({
            "atBats": 4, "hits": 2, "doubles": 1, "triples": 0,
            "homeRuns": 1, "baseOnBalls": 1, "hitByPitch": 0,
            "sacFlies": 0, "strikeOuts": 1,
        })
        assert line["at_bats"] == 4
        assert line["hits"] == 2
        assert line["home_runs"] == 1
        assert line["walks"] == 1
        assert log_row_pa(line) == 5

    def test_pitching_line_uses_outs_for_exact_innings(self):
        # 20 outs = 6 and two-thirds innings.
        line = pitching_line_from_boxscore({
            "outs": 20, "hits": 5, "earnedRuns": 2, "baseOnBalls": 2,
            "strikeOuts": 7, "homeRuns": 1, "hitBatsmen": 1,
            "battersFaced": 28, "gamesStarted": 1,
        })
        assert line["innings_pitched"] == pytest.approx(20 / 3)
        assert line["hit_by_pitch"] == 1
        assert line["batters_faced"] == 28
        assert line["started"] is True

    def test_reliever_line_not_marked_started(self):
        line = pitching_line_from_boxscore({"outs": 3, "battersFaced": 4, "gamesStarted": 0})
        assert line["started"] is False


class TestPlatoonAdvantage:
    def test_opposite_hands_have_edge(self):
        assert platoon_advantage("L", "R") == 1
        assert platoon_advantage("R", "L") == 1

    def test_same_hands_no_edge(self):
        assert platoon_advantage("R", "R") == 0
        assert platoon_advantage("L", "L") == 0

    def test_switch_hitter_always_has_edge(self):
        assert platoon_advantage("S", "R") == 1
        assert platoon_advantage("S", "L") == 1

    def test_missing_handedness_is_unknown(self):
        assert platoon_advantage(None, "R") is None
        assert platoon_advantage("R", None) is None


class TestVsHandHistory:
    def test_empty_history_is_unknown(self):
        history = VsHandHistory()
        snap = history.snapshot(1, "R")
        assert snap["pa"] == 0
        assert snap["hit_per_pa"] is None

    def test_accumulates_by_hand(self):
        history = VsHandHistory()
        history.add(1, "R", hits=2, pa=4)
        history.add(1, "R", hits=0, pa=4)
        history.add(1, "L", hits=3, pa=3)

        vs_right = history.snapshot(1, "R")
        assert vs_right["pa"] == 8
        assert vs_right["hit_per_pa"] == pytest.approx(2 / 8)

        vs_left = history.snapshot(1, "L")
        assert vs_left["pa"] == 3
        assert vs_left["hit_per_pa"] == pytest.approx(1.0)

    def test_snapshot_before_add_prevents_same_day_leakage(self):
        history = VsHandHistory()
        snap = history.snapshot(1, "R")   # feature read happens first
        history.add(1, "R", hits=3, pa=4)  # outcome recorded after
        assert snap["pa"] == 0

    def test_zero_pa_and_missing_hand_are_ignored(self):
        history = VsHandHistory()
        history.add(1, None, hits=1, pa=4)
        history.add(1, "R", hits=0, pa=0)
        assert history.snapshot(1, "R")["pa"] == 0
