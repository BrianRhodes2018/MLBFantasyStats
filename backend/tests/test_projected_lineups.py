import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from projected_lineups import (
    PROJECTED_LINEUP_EDGE_THRESHOLD,
    ProjectedLineupsResult,
    build_lineup_meta,
    candidate_score_floor,
    group_lineups_by_team,
    parse_sportsdataio_starting_lineups,
)


def test_parse_sportsdataio_starting_lineups_filters_to_active_batters():
    rows = [
        {
            "PlayerID": 100,
            "Name": "Aaron Judge",
            "Team": "NYY",
            "BattingOrder": 2,
            "Position": "RF",
            "Starting": True,
            "Confirmed": False,
        },
        {
            "PlayerID": 101,
            "Name": "Injured Bat",
            "Team": "NYY",
            "BattingOrder": 7,
            "Position": "DH",
            "Starting": True,
            "Confirmed": False,
            "InjuryStatus": "Out",
        },
        {
            "PlayerID": 102,
            "Name": "Starting Pitcher",
            "Team": "NYY",
            "BattingOrder": 9,
            "Position": "SP",
            "Starting": True,
            "Confirmed": False,
        },
    ]

    players = parse_sportsdataio_starting_lineups(rows, fetched_at="2026-05-27T12:00:00+00:00")

    assert len(players) == 1
    assert players[0].name == "Aaron Judge"
    assert players[0].team == "New York Yankees"
    assert players[0].lineup_source == "projected"


def test_group_lineups_by_team_uses_full_name_keys():
    players = parse_sportsdataio_starting_lineups([
        {
            "PlayerID": 100,
            "Name": "Aaron Judge",
            "Team": "NYY",
            "BattingOrder": 2,
            "Position": "RF",
            "Starting": True,
            "Confirmed": True,
        }
    ])

    grouped = group_lineups_by_team(players)

    assert list(grouped.keys()) == ["new york yankees"]
    assert grouped["new york yankees"][0].lineup_source == "confirmed"


def test_projected_lineup_score_floor_adds_eight_point_premium():
    assert candidate_score_floor(
        lineup_source="projected",
        min_composite_score=50.0,
        projected_lineup_edge_threshold=PROJECTED_LINEUP_EDGE_THRESHOLD,
    ) == 58.0

    assert candidate_score_floor(
        lineup_source="confirmed",
        min_composite_score=50.0,
        projected_lineup_edge_threshold=PROJECTED_LINEUP_EDGE_THRESHOLD,
    ) == 50.0


def test_build_lineup_meta_handles_empty_not_configured_provider():
    meta = build_lineup_meta(
        lineup_mode="hybrid",
        projected_result=ProjectedLineupsResult(
            players=[],
            provider="sportsdataio",
            status="not_configured",
            message="SPORTSDATAIO_API_KEY is not configured",
        ),
        unresolved_projected_players=[],
        rows=[],
    )

    assert meta["mode"] == "hybrid"
    assert meta["provider"] == "sportsdataio"
    assert meta["status"] == "not_configured"
    assert meta["message"] == "SPORTSDATAIO_API_KEY is not configured"
    assert meta["lineup_counts"] == {"confirmed": 0, "projected": 0}
