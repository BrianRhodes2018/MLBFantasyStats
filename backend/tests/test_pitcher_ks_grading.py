from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from grade_pitcher_ks import grade_from_feed, outcomes_for_predictions
from pitcher_ks import store
from pitcher_ks.store import apply_grades, evaluation_metrics
from routers.pitcher_ks import router


def game_feed(*, detailed_state="Final", games_started=1, strikeouts=7):
    return {
        "gameData": {
            "status": {
                "abstractGameState": "Final" if detailed_state == "Final" else "Preview",
                "detailedState": detailed_state,
            }
        },
        "liveData": {
            "boxscore": {
                "teams": {
                    "away": {
                        "players": {
                            "ID99": {
                                "person": {"id": 99},
                                "stats": {
                                    "pitching": {
                                        "gamesStarted": games_started,
                                        "strikeOuts": strikeouts,
                                        "battersFaced": 24,
                                        "outs": 17,
                                        "pitchesThrown": 91,
                                    }
                                },
                            }
                        }
                    },
                    "home": {"players": {}},
                }
            }
        },
    }


def test_final_starter_is_confirmed_from_official_pitching_line():
    grade = grade_from_feed(game_pk=123, pitcher_id=99, feed=game_feed())

    assert grade["result_status"] == "graded"
    assert grade["started"] == 1
    assert grade["actual_ks"] == 7
    assert grade["actual_batters_faced"] == 24
    assert grade["actual_innings_pitched"] == pytest.approx(17 / 3, abs=0.001)
    assert grade["actual_pitch_count"] == 91


def test_final_relief_appearance_is_did_not_start():
    grade = grade_from_feed(
        game_pk=123,
        pitcher_id=99,
        feed=game_feed(games_started=0, strikeouts=3),
    )

    assert grade["result_status"] == "did_not_start"
    assert grade["started"] == 0
    assert "did not start" in grade["grade_detail"].lower()
    assert "actual_ks" not in grade


@pytest.mark.parametrize(
    ("detailed_state", "expected"),
    [
        ("Postponed", "postponed"),
        ("Suspended", "suspended"),
        ("Cancelled", "cancelled"),
        ("In Progress", "pending"),
    ],
)
def test_nonfinal_game_statuses_are_not_graded(detailed_state, expected):
    grade = grade_from_feed(
        game_pk=123,
        pitcher_id=99,
        feed=game_feed(detailed_state=detailed_state),
    )

    assert grade["result_status"] == expected
    assert "actual_ks" not in grade


class FakeSource:
    def __init__(self, feed):
        self.feed = feed
        self.calls = []

    def game(self, game_pk, *, refresh=False):
        self.calls.append((game_pk, refresh))
        return self.feed


def test_one_refreshed_game_feed_grades_all_projected_pitchers_in_game():
    source = FakeSource(game_feed())
    outcomes = outcomes_for_predictions(
        source,
        [
            {"game_pk": 123, "pitcher_id": 99},
            {"game_pk": 123, "pitcher_id": 100},
        ],
    )

    assert source.calls == [(123, True)]
    assert outcomes[(123, 99)]["result_status"] == "graded"
    assert outcomes[(123, 100)]["result_status"] == "did_not_start"


def projection(projected, actual, *, status="graded", p5=0.6, p6=0.4):
    return {
        "projected_ks": projected,
        "actual_ks": actual,
        "result_status": status,
        "p10_ks": 3,
        "p90_ks": 8,
        "probability_5_plus": p5,
        "probability_6_plus": p6,
    }


def test_live_evaluation_metrics_exclude_dns_and_report_probability_scores():
    metrics = evaluation_metrics([
        projection(6.0, 5),
        projection(5.0, 7),
        projection(4.5, None, status="did_not_start"),
    ])

    assert metrics["graded_starts"] == 2
    assert metrics["did_not_start"] == 1
    assert metrics["complete"] is True
    assert metrics["mae"] == pytest.approx(1.5)
    assert metrics["rmse"] == pytest.approx((2.5) ** 0.5, abs=0.0001)
    assert metrics["bias"] == pytest.approx(-0.5)
    assert metrics["interval_80_coverage"] == 1.0
    assert metrics["brier_5_plus"] is not None


class FakeDatabase:
    def __init__(self, records):
        self.records = records
        self.executed = []

    async def fetch_all(self, _query):
        return self.records

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, query):
        self.executed.append(query)


def stored_row(row_id, approach, *, actual_ks=None):
    return {
        "id": row_id,
        "game_pk": 123,
        "pitcher_id": 99,
        "approach": approach,
        "actual_ks": actual_ks,
        "actual_batters_faced": None,
        "actual_innings_pitched": None,
        "actual_pitch_count": None,
        "result_status": "graded" if actual_ks is not None else "pending",
        "started": None,
        "game_status": None,
        "grading_source": None,
        "grade_detail": None,
        "graded_at": None,
    }


@pytest.mark.asyncio
async def test_apply_grades_updates_all_three_approaches_atomically():
    database = FakeDatabase([
        stored_row(1, "decomposed"),
        stored_row(2, "count"),
        stored_row(3, "bayes"),
    ])
    official = grade_from_feed(game_pk=123, pitcher_id=99, feed=game_feed())

    result = await apply_grades(
        projection_date="2026-08-01",
        outcomes={(123, 99): official},
        db=database,
    )

    assert result["changed_rows"] == 3
    assert result["status_counts"] == {"graded": 3}
    assert len(database.executed) == 3


@pytest.mark.asyncio
async def test_apply_grades_refuses_conflicting_final_without_force():
    database = FakeDatabase([stored_row(1, "decomposed", actual_ks=6)])
    official = grade_from_feed(game_pk=123, pitcher_id=99, feed=game_feed(strikeouts=7))

    with pytest.raises(ValueError, match="conflict"):
        await apply_grades(
            projection_date="2026-08-01",
            outcomes={(123, 99): official},
            db=database,
        )

    assert database.executed == []


@pytest.mark.asyncio
async def test_apply_grades_is_idempotent_for_an_identical_final_result():
    official = grade_from_feed(game_pk=123, pitcher_id=99, feed=game_feed())
    existing = stored_row(1, "decomposed", actual_ks=7)
    existing.update({
        **official,
        "graded_at": "2026-08-02T12:00:00+00:00",
    })
    database = FakeDatabase([existing])

    result = await apply_grades(
        projection_date="2026-08-01",
        outcomes={(123, 99): official},
        db=database,
    )

    assert result["changed_rows"] == 0
    assert database.executed == []


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_pitcher_k_dates_and_historical_routes(client, monkeypatch):
    async def fake_dates(approach, *, limit):
        assert approach == "decomposed"
        assert limit == 30
        return {
            "dates": [{"date": "2026-08-01", "grading_status": "graded"}],
            "latest_date": "2026-08-01",
            "count": 1,
        }

    async def fake_approach(approach, *, projection_date):
        assert approach == "decomposed"
        assert projection_date == "2026-08-01"
        return {"projection_date": projection_date, "predictions": []}

    monkeypatch.setattr(store, "fetch_approach_dates", fake_dates)
    monkeypatch.setattr(store, "fetch_approach", fake_approach)

    dates = client.get("/api/pitcher-ks/approaches/decomposed/dates?limit=30")
    history = client.get("/api/pitcher-ks/approaches/decomposed/2026-08-01")

    assert dates.status_code == 200
    assert dates.json()["data"]["count"] == 1
    assert history.status_code == 200
    assert history.json()["data"]["projection_date"] == "2026-08-01"


def test_pitcher_k_comparison_history_and_ledger_routes(client, monkeypatch):
    async def fake_comparison(*, projection_date):
        assert projection_date == "2026-08-01"
        return {"projection_date": projection_date, "rows": []}

    async def fake_ledger():
        return {"days_graded": 2, "approaches": {"decomposed": {"mae": 1.5}}}

    monkeypatch.setattr(store, "fetch_comparison", fake_comparison)
    monkeypatch.setattr(store, "fetch_ledger_summary", fake_ledger)

    comparison = client.get("/api/pitcher-ks/compare/2026-08-01")
    ledger = client.get("/api/pitcher-ks/ledger")

    assert comparison.status_code == 200
    assert comparison.json()["data"]["projection_date"] == "2026-08-01"
    assert ledger.status_code == 200
    assert ledger.json()["data"]["days_graded"] == 2


def test_pitcher_k_history_routes_validate_inputs(client):
    assert client.get("/api/pitcher-ks/approaches/unknown/dates").status_code == 422
    assert client.get("/api/pitcher-ks/approaches/decomposed/not-a-date").status_code == 422
    assert client.get("/api/pitcher-ks/compare/not-a-date").status_code == 422
