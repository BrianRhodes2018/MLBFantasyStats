from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from pitcher_ks.features import FEATURE_NAMES, PitcherKsDatasetBuilder
from pitcher_ks.modeling import (
    APPROACH_ORDER,
    dataset_profile,
    make_approaches,
)


def synthetic_rows(count: int = 220) -> list[dict]:
    rng = np.random.default_rng(20260801)
    rows = []
    start = date(2024, 3, 20)
    for index in range(count):
        k_rate = float(rng.uniform(0.16, 0.34))
        bf = int(np.clip(rng.normal(23, 3), 12, 34))
        opponent_rate = float(rng.uniform(0.18, 0.30))
        expected = bf * (0.7 * k_rate + 0.3 * opponent_rate)
        strikeouts = int(np.clip(rng.poisson(expected), 0, bf))
        row = {
            "game_date": (start + timedelta(days=index)).isoformat(),
            "game_pk": 100000 + index,
            "pitcher_id": 5000 + (index % 45),
            "strikeouts": strikeouts,
            "batters_faced": bf,
            "pitcher_k_rate": k_rate,
            "pitcher_recent_k_rate": min(0.48, max(0.06, k_rate + rng.normal(0, 0.025))),
            "pitcher_k_rate_trend": rng.normal(0, 0.02),
            "pitcher_starts_log": np.log1p(index % 25),
            "pitcher_bf_avg": float(np.clip(rng.normal(23, 2), 16, 30)),
            "pitcher_recent_bf_avg": float(np.clip(rng.normal(23, 2.5), 15, 31)),
            "pitcher_pitches_avg": float(np.clip(rng.normal(89, 7), 65, 105)),
            "pitcher_recent_pitches_avg": float(np.clip(rng.normal(89, 8), 60, 110)),
            "pitcher_ip_avg": float(np.clip(rng.normal(5.3, 0.7), 3.0, 7.2)),
            "pitcher_days_rest": float(rng.integers(4, 8)),
            "pitcher_vs_opponent_k_rate": k_rate,
            "opponent_lineup_k_rate": opponent_rate,
            "opponent_team_k_rate": opponent_rate,
            "lineup_history_coverage": float(rng.uniform(0.5, 1.0)),
            "lineup_confidence": float(rng.uniform(0.55, 1.0)),
            "is_home_pitcher": float(index % 2),
            "throws_left": float(index % 4 == 0),
            "league_k_rate": 0.225,
            "month_sin": 0.5,
            "month_cos": -0.5,
        }
        assert all(name in row for name in FEATURE_NAMES)
        rows.append(row)
    return rows


@pytest.mark.parametrize("approach", APPROACH_ORDER)
def test_all_approaches_return_coherent_probability_distributions(approach):
    rows = synthetic_rows()
    model = make_approaches()[approach].fit(rows[:180])
    projections = model.predict(rows[180:])

    assert len(projections) == 40
    for projection in projections:
        assert sum(projection.pmf) == pytest.approx(1.0, abs=1e-6)
        assert 0 <= projection.probability_6_plus <= projection.probability_5_plus <= 1
        assert 0 <= projection.p10_ks <= projection.median_ks <= projection.p90_ks
        assert 10 <= projection.projected_batters_faced <= 36


def test_dataset_profile_checks_grain_and_targets():
    rows = synthetic_rows(20)
    profile = dataset_profile(rows)

    assert profile["starts"] == 20
    assert profile["duplicate_identity_rows"] == 0
    assert profile["invalid_target_rows"] == 0
    assert max(profile["missing_rate_by_feature"].values()) == 0


class FakeSource:
    def __init__(self, games_by_date):
        self.games_by_date = games_by_date

    def final_games(self, target):
        return self.games_by_date.get(target, [])


def _slate_game(target: date, game_pk: int, away_starter: int, home_starter: int):
    player_ids = list(range(100, 118))
    players = {
        f"ID{player_id}": {
            "id": player_id,
            "fullName": f"Batter {player_id}",
            "batSide": {"code": "R"},
        }
        for player_id in player_ids
    }
    for starter_id, hand in ((away_starter, "R"), (home_starter, "L")):
        players[f"ID{starter_id}"] = {
            "id": starter_id,
            "fullName": f"Starter {starter_id}",
            "pitchHand": {"code": hand},
        }

    def batter_box(player_id):
        return {
            "person": {"id": player_id, "fullName": f"Batter {player_id}"},
            "stats": {"batting": {
                "plateAppearances": 4,
                "atBats": 4,
                "hits": 1,
                "strikeOuts": 1,
            }},
        }

    def pitcher_box(starter_id, strikeouts):
        return {
            "person": {"id": starter_id, "fullName": f"Starter {starter_id}"},
            "stats": {"pitching": {
                "outs": 18,
                "hits": 5,
                "earnedRuns": 2,
                "baseOnBalls": 2,
                "strikeOuts": strikeouts,
                "homeRuns": 1,
                "hitBatsmen": 0,
                "battersFaced": 24,
                "pitchesThrown": 92,
                "gamesStarted": 1,
            }},
        }

    away_players = {f"ID{pid}": batter_box(pid) for pid in player_ids[:9]}
    home_players = {f"ID{pid}": batter_box(pid) for pid in player_ids[9:]}
    away_players[f"ID{away_starter}"] = pitcher_box(away_starter, 7)
    home_players[f"ID{home_starter}"] = pitcher_box(home_starter, 6)
    return {
        "schedule": {
            "game_id": game_pk,
            "game_type": "R",
            "game_datetime": f"{target.isoformat()}T23:05:00Z",
            "away_name": "Away",
            "home_name": "Home",
            "venue_name": "Test Park",
        },
        "game": {
            "gameData": {"players": players},
            "liveData": {"boxscore": {"teams": {
                "away": {
                    "team": {"name": "Away"},
                    "pitchers": [away_starter],
                    "battingOrder": player_ids[:9],
                    "players": away_players,
                },
                "home": {
                    "team": {"name": "Home"},
                    "pitchers": [home_starter],
                    "battingOrder": player_ids[9:],
                    "players": home_players,
                },
            }}},
        },
    }


def test_builder_never_uses_same_day_start_in_features():
    first = date(2026, 4, 1)
    second = date(2026, 4, 7)
    source = FakeSource({
        first: [_slate_game(first, 1, 900, 901)],
        second: [_slate_game(second, 2, 900, 902)],
    })
    builder = PitcherKsDatasetBuilder(source=source)

    first_rows = builder.rows_for_date(first)
    second_rows = builder.rows_for_date(second)

    first_start = next(row for row in first_rows if row["pitcher_id"] == 900)
    second_start = next(row for row in second_rows if row["pitcher_id"] == 900)
    assert first_start["pitcher_starts_log"] == 0
    assert second_start["pitcher_starts_log"] == pytest.approx(np.log1p(1))
