import polars as pl

from hit_model.benchmark import clustered_rate_interval, ranked_top_n_rows


def test_date_cluster_bootstrap_is_deterministic_and_reports_mde():
    daily = pl.DataFrame({
        "game_date": ["2026-06-01", "2026-06-02", "2026-06-03"],
        "hits": [7, 6, 8],
        "picks": [10, 10, 10],
    })
    first = clustered_rate_interval(
        daily,
        iterations=2000,
        confidence_level=0.95,
        seed=42,
    )
    second = clustered_rate_interval(
        daily,
        iterations=2000,
        confidence_level=0.95,
        seed=42,
    )
    assert first == second
    assert first["rate"] == 0.7
    assert first["game_dates"] == 3
    assert first["minimum_detectable_improvement_80pct_power"] > 0


def test_top_n_ranking_uses_raw_probability_for_calibration_ties():
    predictions = pl.DataFrame({
        "game_date": ["2026-06-01"] * 3,
        "player_id": [1, 2, 3],
        "got_hit": [0, 1, 1],
        "probability": [0.7, 0.7, 0.6],
        "raw_probability": [0.68, 0.72, 0.65],
    })
    selected = ranked_top_n_rows(predictions, 2)
    assert selected["player_id"].to_list() == [2, 1]
    assert selected["rank"].to_list() == [1, 2]
