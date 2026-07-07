import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from projected_lineups import (
    PROJECTED_LINEUP_EDGE_THRESHOLD,
    ProjectedLineupsResult,
    build_lineup_meta,
    build_recent_mlb_lineup_projections,
    candidate_score_floor,
    group_lineups_by_team,
    parse_sportsdataio_starting_lineups,
    team_key,
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


def _player(player_id, name, position="OF"):
    return {
        "person": {"id": player_id, "fullName": name},
        "position": {"abbreviation": position},
    }


def _boxscore_team(name, order, player_names):
    return {
        "team": {"name": name},
        "battingOrder": order,
        "players": {
            f"ID{player_id}": _player(player_id, player_names[player_id])
            for player_id in order
        },
    }


def _game(game_id, game_date, yankees_order, pitcher_hand="R"):
    yankee_names = {
        1: "Leadoff Lock",
        2: "Two Hole Regular",
        3: "Middle Bat",
        4: "Cleanup Bat",
        5: "Fifth Bat",
        6: "Sixth Bat",
        7: "Seventh Bat",
        8: "Eighth Bat",
        9: "Ninth Bat",
        10: "Spot Starter",
    }
    red_sox_order = [101, 102, 103, 104, 105, 106, 107, 108, 109]
    red_sox_names = {pid: f"Boston {pid}" for pid in red_sox_order}
    return {
        "gameData": {
            "game": {"pk": game_id},
            "datetime": {"officialDate": game_date},
            "teams": {
                "away": {"name": "New York Yankees"},
                "home": {"name": "Boston Red Sox"},
            },
            "probablePitchers": {
                "away": {"id": 901},
                "home": {"id": 902},
            },
            "players": {
                "ID901": {"pitchHand": {"code": "L"}},
                "ID902": {"pitchHand": {"code": pitcher_hand}},
            },
        },
        "liveData": {
            "boxscore": {
                "teams": {
                    "away": _boxscore_team("New York Yankees", yankees_order, yankee_names),
                    "home": _boxscore_team("Boston Red Sox", red_sox_order, red_sox_names),
                }
            }
        },
    }


def test_recent_mlb_lineup_projection_uses_last_14_day_batting_orders():
    games = [
        _game(1, "2026-05-20", [1, 2, 3, 4, 5, 6, 7, 8, 9]),
        _game(2, "2026-05-21", [1, 2, 3, 4, 5, 6, 7, 8, 9]),
        _game(3, "2026-05-22", [1, 2, 3, 4, 5, 6, 7, 8, 9]),
        _game(4, "2026-05-23", [1, 10, 3, 4, 5, 6, 7, 8, 9]),
    ]

    result = build_recent_mlb_lineup_projections(
        games,
        target_date="2026-05-27",
        target_team_keys={team_key("New York Yankees")},
        opposing_throws_by_team_key={team_key("New York Yankees"): "R"},
        min_confidence=0.5,
        fetched_at="2026-05-27T12:00:00+00:00",
    )

    assert result.provider == "mlb_recent_lineups"
    assert result.status == "ok"
    assert result.meta["lookback_days"] == 14
    assert result.meta["games_used"] == 4
    yankees = group_lineups_by_team(result.players)[team_key("New York Yankees")]
    assert [player.name for player in yankees[:2]] == ["Leadoff Lock", "Two Hole Regular"]
    assert yankees[0].batting_order == 1
    assert yankees[0].confidence == 1.0
    assert yankees[1].confidence == 0.75
    assert yankees[1].split == "vs RHP"


def test_slot_bouncing_regular_is_not_diluted_out():
    """Regression (the Nathan Lukes case): a player who starts most games
    but bounces between slots must still project. The old per-slot
    selection let slot-specific regulars squeeze him out entirely."""
    # Player 10 starts 5 of 7 games but at three different slots (1, 2, 8).
    # Player 1 or 2 holds the other slot whenever 10 moves.
    games = [
        _game(1, "2026-05-20", [1, 2, 3, 4, 5, 6, 7, 8, 9]),
        _game(2, "2026-05-21", [1, 2, 3, 4, 5, 6, 7, 10, 9]),   # 10 at slot 8
        _game(3, "2026-05-22", [1, 10, 3, 4, 5, 6, 7, 8, 9]),   # 10 at slot 2
        _game(4, "2026-05-23", [1, 2, 3, 4, 5, 6, 7, 8, 9]),
        _game(5, "2026-05-24", [10, 2, 3, 4, 5, 6, 7, 8, 9]),   # 10 leads off
        _game(6, "2026-05-25", [10, 2, 3, 4, 5, 6, 7, 8, 9]),   # 10 leads off
        _game(7, "2026-05-26", [10, 2, 3, 4, 5, 6, 7, 8, 9]),   # 10 leads off
    ]
    result = build_recent_mlb_lineup_projections(
        games,
        target_date="2026-05-27",
        target_team_keys={team_key("New York Yankees")},
        opposing_throws_by_team_key={team_key("New York Yankees"): "R"},
        min_confidence=0.5,
        fetched_at="2026-05-27T12:00:00+00:00",
    )
    yankees = group_lineups_by_team(result.players)[team_key("New York Yankees")]
    names = [player.name for player in yankees]
    assert "Spot Starter" in names          # player 10 — 5 of 7 starts
    # Recent leadoff streak -> projected at/near the top of the order.
    assert names.index("Spot Starter") <= 1


def test_projection_matches_hit_picks_formula():
    """The provider and predict_hits_today.project_lineup must produce
    the identical batting order from the same games (shared core)."""
    from datetime import date as date_cls

    from predict_hits_today import project_lineup

    games = [
        _game(1, "2026-05-20", [1, 2, 3, 4, 5, 6, 7, 8, 9]),
        _game(2, "2026-05-24", [10, 2, 3, 4, 5, 6, 7, 8, 9]),
        _game(3, "2026-05-25", [10, 2, 3, 4, 5, 6, 7, 8, 9]),
        _game(4, "2026-05-26", [1, 10, 3, 4, 5, 6, 7, 8, 9]),
    ]
    result = build_recent_mlb_lineup_projections(
        games,
        target_date="2026-05-27",
        target_team_keys={team_key("New York Yankees")},
        opposing_throws_by_team_key={team_key("New York Yankees"): "R"},
        min_confidence=0.0,   # no display floor — compare full selections
        fetched_at="2026-05-27T12:00:00+00:00",
    )
    yankees = group_lineups_by_team(result.players)[team_key("New York Yankees")]
    provider_order = [player.provider_player_id for player in yankees]

    entries = [
        {"date": f"2026-05-{d}", "opp_hand": "R", "order": order}
        for d, order in (("20", [1, 2, 3, 4, 5, 6, 7, 8, 9]),
                         ("24", [10, 2, 3, 4, 5, 6, 7, 8, 9]),
                         ("25", [10, 2, 3, 4, 5, 6, 7, 8, 9]),
                         ("26", [1, 10, 3, 4, 5, 6, 7, 8, 9]))
    ]
    picks_order, _ = project_lineup(entries, "R", date_cls(2026, 5, 27))
    assert provider_order == picks_order
