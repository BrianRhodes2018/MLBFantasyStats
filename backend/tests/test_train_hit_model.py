"""Unit tests for the evaluation helpers in train_hit_model.py."""

from datetime import date

import numpy as np
import polars as pl
import pytest

from train_hit_model import FoldResult, naive_scores, pooled_summary, top_n_hit_rates


def make_test_frame():
    # Two days, three hitters each. Scores rank a hit-getter first on day 1
    # and a no-hit batter first on day 2.
    return pl.DataFrame({
        "game_date": ["2026-06-01"] * 3 + ["2026-06-02"] * 3,
        "got_hit": [1, 0, 1, 0, 1, 0],
    })


class TestTopNHitRates:
    def test_picks_top_scores_per_day(self):
        df = make_test_frame()
        scores = np.array([0.9, 0.5, 0.1, 0.8, 0.6, 0.2])
        out = top_n_hit_rates(df, scores, top_ns=(1, 2))
        # top1: day1 pick got a hit, day2 pick did not -> 0.5 over 2 picks
        assert out["top1_hit_rate"] == pytest.approx(0.5)
        assert out["top1_picks"] == 2
        # top2 pool: day1 (1, 0), day2 (0, 1) -> 2/4
        assert out["top2_hit_rate"] == pytest.approx(0.5)
        assert out["top2_picks"] == 4

    def test_infinite_scores_are_never_picked(self):
        df = make_test_frame()
        scores = np.array([float("-inf")] * 5 + [0.9])
        out = top_n_hit_rates(df, scores, top_ns=(2,))
        # Only one finite score exists (day 2, got_hit=0).
        assert out["top2_picks"] == 1
        assert out["top2_hit_rate"] == pytest.approx(0.0)


class TestNaiveScores:
    def test_requires_slot_and_sample(self):
        df = pl.DataFrame({
            "batting_order": [1, 2, 8, 3],
            "season_pa": [200, 50, 300, 150],
            "season_hit_per_pa": [0.30, 0.35, 0.40, None],
        })
        scores = naive_scores(df)
        assert scores[0] == pytest.approx(0.30)   # qualifies
        assert scores[1] == float("-inf")          # too few PA
        assert scores[2] == float("-inf")          # lineup slot too low
        assert scores[3] == float("-inf")          # missing rate


class TestPooledSummary:
    def test_weights_by_pick_counts(self):
        folds = [
            FoldResult("m", "a", "b", 100, 50, {
                "top5_hit_rate": 0.60, "top5_picks": 10, "auc": 0.55,
            }),
            FoldResult("m", "b", "c", 100, 150, {
                "top5_hit_rate": 0.80, "top5_picks": 30, "auc": 0.65,
            }),
        ]
        pooled = pooled_summary(folds)
        # (0.6*10 + 0.8*30) / 40 = 0.75
        assert pooled["top5_hit_rate"] == pytest.approx(0.75)
        assert pooled["top5_picks"] == 40
        # AUC weighted by n_test: (0.55*50 + 0.65*150) / 200 = 0.625
        assert pooled["auc"] == pytest.approx(0.625)


class TestProjectLineup:
    TARGET = date(2026, 7, 7)

    def entry(self, day, hand, order, month=7):
        return {"date": f"2026-{month:02d}-{day:02d}", "opp_hand": hand, "order": order}

    def test_recent_role_change_beats_stale_bench_stretch(self):
        # The "Nathan Lukes case": player 99 was absent in older lineups
        # (weight 1.0 each) but has started the last three games (weight
        # 2.0 each) — the projection must include him near the top.
        from predict_hits_today import project_lineup

        old_order = list(range(1, 10))            # 1..9, no player 99
        new_order = [99] + list(range(2, 10))     # 99 leads off, bumps player 1
        lineups = (
            [self.entry(d, "R", old_order, month=6) for d in range(23, 27)]  # 4 old games
            + [self.entry(d, "R", new_order) for d in range(5, 8)]           # 3 recent games
        )
        order, source = project_lineup(lineups, "R", self.TARGET)
        assert 99 in order
        assert order[0] == 99 or order[1] == 99  # weighted avg slot puts him up top
        assert "recency-weighted" in source

    def test_prefers_same_hand_subset_when_large_enough(self):
        from predict_hits_today import project_lineup

        vs_l = [self.entry(d, "L", list(range(11, 20))) for d in (1, 2, 3)]
        vs_r = [self.entry(d, "R", list(range(1, 10))) for d in (4, 5, 6)]
        order, source = project_lineup(vs_l + vs_r, "L", self.TARGET)
        assert order == list(range(11, 20))
        assert "vs LHP" in source

    def test_small_same_hand_sample_uses_all_games(self):
        from predict_hits_today import project_lineup

        # Only 2 games vs LHP (< MIN_SAME_HAND_GAMES) — pool is everything.
        lineups = [
            self.entry(1, "L", list(range(11, 20))),
            self.entry(2, "L", list(range(11, 20))),
            self.entry(5, "R", list(range(1, 10))),
            self.entry(6, "R", list(range(1, 10))),
        ]
        _, source = project_lineup(lineups, "L", self.TARGET)
        assert "all recent games" in source

    def test_orders_by_weighted_average_slot(self):
        from predict_hits_today import project_lineup

        # Same nine players every game, fixed slots -> projection must
        # reproduce the batting order exactly.
        fixed = [30, 10, 50, 20, 70, 40, 90, 60, 80]
        lineups = [self.entry(d, "R", fixed) for d in (4, 5, 6)]
        order, _ = project_lineup(lineups, "R", self.TARGET)
        assert order == fixed

    def test_empty_history(self):
        from predict_hits_today import project_lineup

        order, source = project_lineup([], "R", self.TARGET)
        assert order is None
        assert source == "none"
