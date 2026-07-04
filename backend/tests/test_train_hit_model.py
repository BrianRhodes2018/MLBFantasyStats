"""Unit tests for the evaluation helpers in train_hit_model.py."""

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


class TestChooseProjectedLineup:
    def make_lineups(self):
        return [
            {"date": "2026-07-01", "opp_hand": "L", "order": list(range(1, 10))},
            {"date": "2026-07-02", "opp_hand": "R", "order": list(range(11, 20))},
            {"date": "2026-07-03", "opp_hand": "R", "order": list(range(21, 30))},
        ]

    def test_prefers_most_recent_same_hand(self):
        from predict_hits_today import choose_projected_lineup

        order, source = choose_projected_lineup(self.make_lineups(), "L")
        assert order == list(range(1, 10))
        assert "vs LHP" in source

    def test_falls_back_to_most_recent(self):
        from predict_hits_today import choose_projected_lineup

        # No lineup vs an S-hand exists; use the latest overall.
        order, source = choose_projected_lineup(self.make_lineups(), None)
        assert order == list(range(21, 30))
        assert "most recent" in source

    def test_empty_history(self):
        from predict_hits_today import choose_projected_lineup

        order, source = choose_projected_lineup([], "R")
        assert order is None
        assert source == "none"
