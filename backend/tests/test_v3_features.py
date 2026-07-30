from datetime import date

import pytest

from build_hit_dataset import HitDatasetBuilder
from hit_model.v3_features import (
    PitchAggregate,
    V3FeatureHistory,
    bullpen_workload_features,
    pitch_family,
    shrunk_rate,
    starter_workload_features,
    summarize_recent_team_games,
)


def pitch_event(
    *,
    code="FF",
    description="In play, no out",
    in_play=True,
    exit_velocity=101.0,
):
    return {
        "isPitch": True,
        "details": {
            "description": description,
            "isInPlay": in_play,
            "type": {"code": code},
        },
        "pitchData": {
            "startSpeed": 95.0,
            "extension": 6.2,
            "breaks": {
                "breakHorizontal": -7.0,
                "breakVerticalInduced": 15.0,
            },
        },
        "hitData": {"launchSpeed": exit_velocity} if in_play else {},
    }


def game_feed(*, result="single", events=None):
    return {
        "liveData": {
            "plays": {
                "allPlays": [{
                    "matchup": {
                        "batter": {"id": 1},
                        "pitcher": {"id": 99},
                        "pitchHand": {"code": "R"},
                    },
                    "result": {"eventType": result},
                    "playEvents": events or [pitch_event()],
                }]
            }
        }
    }


class TestPitchAggregate:
    def test_rates_use_their_actual_denominators(self):
        aggregate = PitchAggregate()
        aggregate.add(
            swing=True,
            whiff=False,
            in_play=True,
            hit_on_bip=True,
            exit_velocity=100.0,
            velocity=95.0,
            horizontal_break=-7.0,
            induced_vertical_break=15.0,
            extension=6.0,
        )
        aggregate.add(
            swing=True,
            whiff=True,
            in_play=False,
            hit_on_bip=False,
            exit_velocity=None,
            velocity=96.0,
            horizontal_break=-8.0,
            induced_vertical_break=16.0,
            extension=6.2,
        )
        snapshot = aggregate.snapshot()
        assert snapshot["contact_rate"] == pytest.approx(0.5)
        assert snapshot["whiff_rate"] == pytest.approx(0.5)
        assert snapshot["bip_hit_rate"] == pytest.approx(1.0)
        assert snapshot["hard_hit_rate"] == pytest.approx(1.0)


class TestV3FeatureHistory:
    def test_game_updates_contact_pitcher_and_arsenal_histories(self):
        history = V3FeatureHistory()
        history.add_game(game_feed())
        features = history.features(
            batter_id=1,
            pitcher_id=99,
            pitcher_hand="R",
        )
        assert features["batter_pitch_sample"] == 1
        assert features["pitcher_pitch_sample"] == 1
        assert features["batter_bip_hit_rate"] == pytest.approx(1.0)
        assert features["arsenal_match_bip_hit"] is not None
        assert features["arsenal_coverage"] == pytest.approx(1.0)
        assert features["arsenal_matchup_missing"] == 0

    def test_snapshot_before_update_is_point_in_time_safe(self):
        history = V3FeatureHistory()
        before = history.features(
            batter_id=1,
            pitcher_id=99,
            pitcher_hand="R",
        )
        history.add_game(game_feed())
        assert before["batter_pitch_sample"] == 0
        assert before["arsenal_matchup_missing"] == 1

    def test_whiffs_are_not_counted_as_contact(self):
        history = V3FeatureHistory()
        history.add_game(game_feed(
            result="strikeout",
            events=[pitch_event(
                description="Swinging Strike",
                in_play=False,
                exit_velocity=None,
            )],
        ))
        features = history.contact_features(1, "R")
        assert features["batter_contact_rate"] == pytest.approx(0.0)
        assert features["batter_whiff_rate"] == pytest.approx(1.0)


class TestV3FeatureHelpers:
    def test_v3_history_can_be_disabled_for_the_v2_daily_path(self):
        builder = HitDatasetBuilder(
            db=None,
            source=None,
            include_v3_features=False,
        )
        assert builder.v3_history is None

    def test_pitch_codes_map_to_stable_families(self):
        assert pitch_family("FF") == "four_seam"
        assert pitch_family("ST") == "slider"
        assert pitch_family("unknown") == "other"

    def test_shrinkage_respects_sample_size(self):
        small = shrunk_rate(0.8, 5, 0.5, strength=40)
        large = shrunk_rate(0.8, 500, 0.5, strength=40)
        assert small < large < 0.8
        assert shrunk_rate(None, 0, 0.5, strength=40) == 0.5

    def test_team_opportunity_uses_recent_games(self):
        rows = [
            {"plate_appearances": 36, "runs": 3, "hits": 7},
            {"plate_appearances": 42, "runs": 5, "hits": 11},
        ]
        features = summarize_recent_team_games(rows)
        assert features["team_recent_pa_per_game"] == pytest.approx(39.0)
        assert features["team_recent_games"] == 2

    def test_starter_workload_and_bullpen_fatigue_are_pregame_only(self):
        starter = starter_workload_features(
            [{
                "game_date": "2026-07-26",
                "started": True,
                "pitches_thrown": 90,
                "batters_faced": 24,
                "innings_pitched": 6.0,
            }],
            target_date=date(2026, 7, 30).isoformat(),
        )
        assert starter["starter_days_rest"] == 4
        assert starter["starter_last3_pitches"] == pytest.approx(90.0)

        bullpen = bullpen_workload_features(
            [
                {
                    "game_date": "2026-07-29",
                    "pitcher_id": 7,
                    "pitches_thrown": 22,
                },
                {
                    "game_date": "2026-07-27",
                    "pitcher_id": 8,
                    "pitches_thrown": 30,
                },
                {
                    "game_date": "2026-07-26",
                    "pitcher_id": 9,
                    "pitches_thrown": 40,
                },
            ],
            target_date="2026-07-30",
        )
        assert bullpen["bullpen_pitches_yesterday"] == 22
        assert bullpen["bullpen_pitches_last3_days"] == 52
        assert bullpen["bullpen_relievers_last3_days"] == 2
