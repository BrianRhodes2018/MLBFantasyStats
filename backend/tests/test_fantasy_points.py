import polars as pl

from espn_fantasy import compute_fantasy_points_batters


def test_compute_espn_batter_fantasy_points_counts_standard_and_derived_stats():
    df = pl.DataFrame(
        [
            {
                "name": "Test Slugger",
                "hits": 10,
                "doubles": 2,
                "triples": 1,
                "home_runs": 3,
                "rbi": 8,
                "strikeouts": 4,
            }
        ]
    )

    result = compute_fantasy_points_batters(
        df,
        {
            "5": 5.0,    # HR
            "6": 1.0,    # XBH = 2B + 3B + HR
            "7": 0.5,    # Singles = H - 2B - 3B - HR
            "21": 1.0,   # RBI
            "27": -1.0,  # Strikeouts
        },
    )

    fantasy_pts = result.select("fantasy_pts").item()

    # HR: 15, XBH: 6, singles: 2, RBI: 8, K: -4
    assert fantasy_pts == 27.0


def test_compute_espn_batter_fantasy_points_handles_empty_frames():
    result = compute_fantasy_points_batters(pl.DataFrame(), {"5": 5.0})

    assert "fantasy_pts" in result.columns
    assert result.is_empty()
