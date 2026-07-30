from hit_model.point_in_time import (
    PointInTimeParkFactors,
    filter_prior_lineups,
    project_lineup_point_in_time,
)


def test_park_factor_never_uses_same_or_later_season_results():
    store = PointInTimeParkFactors({
        "neutral_fallback": {"runs": 100, "hr": 100, "source": "neutral"},
        "snapshots": [
            {
                "effective_date": "2024-01-01",
                "source_season": 2023,
                "factors": {"Test Park": {"runs": 95, "hr": 97}},
            },
            {
                "effective_date": "2025-01-01",
                "source_season": 2024,
                "factors": {"Test Park": {"runs": 110, "hr": 111}},
            },
        ],
    })
    factor = store.lookup("Test Park", "2024-06-01")
    assert factor["runs"] == 95
    assert factor["source_season"] == 2023


def test_unknown_historical_venue_is_neutral_not_current_estimate():
    store = PointInTimeParkFactors({
        "neutral_fallback": {
            "runs": 100,
            "hr": 100,
            "source": "neutral_unavailable",
        },
        "snapshots": [
            {
                "effective_date": "2025-01-01",
                "source_season": 2024,
                "factors": {},
            },
        ],
    })
    factor = store.lookup("Future Stadium", "2025-07-01")
    assert factor["runs"] == 100
    assert factor["source"] == "neutral_unavailable"


def test_lineup_projection_rejects_same_day_and_old_entries():
    entries = [
        {"date": "2026-05-01", "opp_hand": "R", "order": list(range(1, 10))},
        {"date": "2026-05-27", "opp_hand": "R", "order": list(range(1, 10))},
        {"date": "2026-06-01", "opp_hand": "R", "order": list(range(10, 19))},
    ]
    prior = filter_prior_lineups(entries, "2026-06-01", lookback_days=14)
    assert [row["date"] for row in prior] == ["2026-05-27"]
    projection = project_lineup_point_in_time(
        entries,
        "R",
        "2026-06-01",
        lookback_days=14,
    )
    assert projection["order"] == list(range(1, 10))
