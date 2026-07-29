"""Tests for grade_hit_picks.py, hit_picks_store.py, and the /hit-picks routes."""

from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import hit_picks_store
from grade_hit_picks import grade_candidates, outcomes_for_date, summarize_ledger
from hit_model.cohort import freeze_candidate_cohort
from hit_picks_store import (
    apply_grades,
    replace_picks,
    shape_pick_rows,
    summarize_available_dates,
    summarize_pick_rows,
)
from routers.hit_picks import router


def make_candidates():
    # Saved pick lists are sorted by predicted probability descending.
    return [
        {"game_pk": 100, "player_id": 1, "player_name": "A"},
        {"game_pk": 100, "player_id": 2, "player_name": "B"},
        {"game_pk": 100, "player_id": 3, "player_name": "C"},
        {"game_pk": 100, "player_id": 4, "player_name": "D"},
    ]


class TestGradeCandidates:
    def test_counts_hits_among_played(self):
        outcomes = {
            1: {"hits": 2, "pa": 4},   # hit
            2: {"hits": 0, "pa": 4},   # played, no hit
            3: {"hits": 1, "pa": 3},   # hit
            # player 4 did not play
        }
        grades = grade_candidates(make_candidates(), outcomes, top_ns=(2, 4))
        assert grades["top2"] == {"picks": 2, "played": 2, "hits": 1}
        assert grades["top4"] == {"picks": 4, "played": 3, "hits": 2}

    def test_no_outcomes_means_nobody_played(self):
        grades = grade_candidates(make_candidates(), {}, top_ns=(4,))
        assert grades["top4"] == {"picks": 4, "played": 0, "hits": 0}


class TestOutcomeStatlines:
    def test_extracts_full_batting_line(self):
        class Source:
            def final_games(self, target, refresh_schedule):
                assert target == date(2026, 7, 4)
                assert refresh_schedule is True
                return [{
                    "schedule": {"game_id": 777},
                    "game": {
                        "liveData": {
                            "boxscore": {
                                "teams": {
                                    "away": {
                                        "players": {
                                            "ID42": {
                                                "person": {"id": 42},
                                                "stats": {
                                                    "batting": {
                                                        "plateAppearances": 5,
                                                        "atBats": 4,
                                                        "hits": 2,
                                                        "doubles": 1,
                                                        "triples": 0,
                                                        "homeRuns": 1,
                                                        "runs": 2,
                                                        "rbi": 3,
                                                        "baseOnBalls": 1,
                                                        "strikeOuts": 1,
                                                        "totalBases": 6,
                                                    }
                                                },
                                            }
                                        }
                                    },
                                    "home": {"players": {}},
                                }
                            }
                        }
                    }
                }]

        assert outcomes_for_date(Source(), date(2026, 7, 4))[(777, 42)] == {
            "hits": 2,
            "at_bats": 4,
            "plate_appearances": 5,
            "doubles": 1,
            "triples": 0,
            "home_runs": 1,
            "runs": 2,
            "rbi": 3,
            "walks": 1,
            "strikeouts": 1,
            "total_bases": 6,
        }

    def test_keeps_doubleheader_statlines_separate(self):
        class Source:
            def final_games(self, target, refresh_schedule):
                def game(game_pk, hits):
                    return {
                        "schedule": {"game_id": game_pk},
                        "game": {
                            "liveData": {
                                "boxscore": {
                                    "teams": {
                                        "away": {
                                            "players": {
                                                "ID42": {
                                                    "person": {"id": 42},
                                                    "stats": {
                                                        "batting": {
                                                            "plateAppearances": 4,
                                                            "atBats": 4,
                                                            "hits": hits,
                                                        }
                                                    },
                                                }
                                            }
                                        },
                                        "home": {"players": {}},
                                    }
                                }
                            }
                        },
                    }

                return [game(701, 0), game(702, 2)]

        outcomes = outcomes_for_date(Source(), date(2026, 7, 4))
        assert outcomes[(701, 42)]["hits"] == 0
        assert outcomes[(702, 42)]["hits"] == 2
        grades = grade_candidates(
            [
                {"game_pk": 701, "player_id": 42},
                {"game_pk": 702, "player_id": 42},
            ],
            outcomes,
            top_ns=(2,),
        )
        assert grades["top2"] == {"picks": 2, "played": 2, "hits": 1}
        ambiguous_legacy = grade_candidates(
            [{"player_id": 42}], outcomes, top_ns=(1,)
        )
        assert ambiguous_legacy["top1"] == {"picks": 1, "played": 0, "hits": 0}


class TestSummarizeLedger:
    def test_aggregates_by_model_version(self):
        ledger = {"entries": {
            "2026-07-04": {
                "model_version": "hit_gbm_v2",
                "grades": {"top5": {"picks": 5, "played": 5, "hits": 3},
                           "top10": {"picks": 10, "played": 10, "hits": 6},
                           "top15": {"picks": 15, "played": 15, "hits": 9}},
            },
            "2026-07-05": {
                "model_version": "hit_gbm_v2",
                "grades": {"top5": {"picks": 5, "played": 4, "hits": 4},
                           "top10": {"picks": 10, "played": 9, "hits": 7},
                           "top15": {"picks": 15, "played": 14, "hits": 10}},
            },
        }}
        summary = summarize_ledger(ledger)
        agg = summary["hit_gbm_v2"]
        assert agg["days"] == 2
        assert agg["top10"] == {"played": 19, "hits": 13, "hit_rate": pytest.approx(13 / 19, abs=1e-4)}


def pick_row(rank, *, played=1, got_hit=0, version="hit_gbm_v2", date="2026-07-05"):
    return {
        "model_version": version, "pick_date": date, "rank": rank,
        "played": played, "got_hit": got_hit,
    }


class TestSummarizePickRows:
    def test_buckets_by_rank_thresholds(self):
        # Ranks 1-10: ranks 1-5 all hit; 6-10 all miss.
        rows = [pick_row(r, got_hit=1 if r <= 5 else 0) for r in range(1, 11)]
        summary = summarize_pick_rows(rows)
        agg = summary["hit_gbm_v2"]
        assert agg["days"] == 1
        assert agg["top5"] == {"played": 5, "hits": 5, "hit_rate": 1.0}
        assert agg["top10"] == {"played": 10, "hits": 5, "hit_rate": 0.5}

    def test_unplayed_and_ungraded_rows_are_excluded(self):
        rows = [
            pick_row(1, got_hit=1),
            pick_row(2, played=0, got_hit=None),   # scratched — not in denominator
            pick_row(3, played=None, got_hit=None),  # not graded yet — ignored entirely
        ]
        agg = summarize_pick_rows(rows)["hit_gbm_v2"]
        assert agg["top5"] == {"played": 1, "hits": 1, "hit_rate": 1.0}

    def test_versions_tracked_separately(self):
        rows = [
            pick_row(1, got_hit=1, version="hit_logistic_v1", date="2026-07-04"),
            pick_row(1, got_hit=0, version="hit_gbm_v2", date="2026-07-05"),
        ]
        summary = summarize_pick_rows(rows)
        assert summary["hit_logistic_v1"]["top5"]["hit_rate"] == 1.0
        assert summary["hit_gbm_v2"]["top5"]["hit_rate"] == 0.0


def full_pick_row(**overrides):
    row = {
        "run_id": "3e77a1fa-6ff8-4821-aa20-ee51687b483c",
        "pick_date": "2026-07-05",
        "model_version": "hit_gbm_v2",
        "is_public": 1,
        "is_evaluation": 1,
        "generated_at": "2026-07-05T12:00:00+00:00",
        "as_of_timestamp": "2026-07-05T11:55:00+00:00",
        "prediction_mode": "official",
        "candidate_cohort_id": "a" * 64,
        "candidate_count": 18,
        "trained_on_rows": 149241,
        "rank": 1,
        "game_pk": 777,
        "player_id": 1,
        "player_name": "A",
        "team": "DET",
        "opponent": "CLE",
        "venue": "Comerica Park",
        "batting_order": 1,
        "bats": "L",
        "pitcher_id": 2,
        "pitcher_name": "Pitcher",
        "pitcher_throws": "R",
        "lineup_source": "confirmed",
        "hit_probability": 0.72,
        "season_hit_per_pa": 0.28,
        "last10_hit_per_pa": 0.31,
        "platoon_advantage": 1,
        "played": 1,
        "got_hit": 1,
        "hits": 2,
        "at_bats": 4,
        "plate_appearances": 5,
        "doubles": 1,
        "triples": 0,
        "home_runs": 0,
        "runs": 1,
        "rbi": 1,
        "walks": 1,
        "strikeouts": 0,
        "total_bases": 3,
    }
    row.update(overrides)
    return row


class TestHistoryPayloads:
    def test_shapes_statline_and_model_metadata(self):
        payload = shape_pick_rows(
            [full_pick_row()],
            available_models=[
                {"model_version": "hit_gbm_v2", "is_public": True},
                {"model_version": "hit_gbm_v3", "is_public": False},
            ],
        )
        assert payload["grading_status"] == "graded"
        assert payload["picks"][0]["at_bats"] == 4
        assert payload["picks"][0]["home_runs"] == 0
        assert payload["picks"][0]["game_pk"] == 777
        assert payload["run_id"] == "3e77a1fa-6ff8-4821-aa20-ee51687b483c"
        assert payload["candidate_cohort_id"] == "a" * 64
        assert len(payload["available_models"]) == 2

    def test_summarizes_calendar_dates(self):
        rows = [
            full_pick_row(rank=1, got_hit=1),
            full_pick_row(rank=2, player_id=2, got_hit=0),
            full_pick_row(
                pick_date="2026-07-06",
                rank=1,
                played=None,
                got_hit=None,
                hits=None,
            ),
        ]
        payload = summarize_available_dates(rows)
        assert payload["latest_date"] == "2026-07-06"
        assert payload["dates"][0]["grading_status"] == "pending"
        assert payload["dates"][1]["grading_status"] == "graded"
        assert payload["dates"][1]["played"] == 2
        assert payload["dates"][1]["hits"] == 1


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePicksDatabase:
    def __init__(self, rows=None, fetch_one_row=None):
        self.rows = rows or []
        self.fetch_one_row = fetch_one_row
        self.executed = []
        self.inserted = []

    def transaction(self):
        return FakeTransaction()

    async def execute(self, query):
        self.executed.append(query)

    async def execute_many(self, query, rows):
        self.inserted.extend(rows)

    async def fetch_all(self, query):
        return self.rows

    async def fetch_one(self, query):
        return self.fetch_one_row


class TestHistoryWrites:
    @pytest.mark.asyncio
    async def test_public_write_appends_an_immutable_run(self, monkeypatch):
        database = FakePicksDatabase()

        async def fake_database():
            return database

        monkeypatch.setattr(hit_picks_store, "get_picks_db", fake_database)
        inserted = await replace_picks(
            pick_date="2026-07-05",
            model_version="hit_gbm_v3",
            generated_at="2026-07-05T12:00:00+00:00",
            trained_on_rows=150000,
            candidates=[full_pick_row()],
            is_public=True,
        )

        assert inserted == 1
        # Demote public pick/run pointers, demote the previous evaluation
        # pointer, then insert the new run. No prediction DELETE is issued.
        assert len(database.executed) == 4
        assert database.inserted[0]["is_public"] == 1
        assert database.inserted[0]["game_pk"] == 777
        assert all("DELETE" not in str(query).upper() for query in database.executed)
        insert_sql = str(database.executed[-1])
        assert "INSERT INTO hit_pick_runs" in insert_sql

    @pytest.mark.asyncio
    async def test_retrying_same_run_id_is_idempotent(self, monkeypatch):
        candidates = [full_pick_row()]
        cohort_id = freeze_candidate_cohort(candidates)["candidate_cohort_id"]
        existing = {
            "run_id": "3e77a1fa-6ff8-4821-aa20-ee51687b483c",
            "pick_date": "2026-07-05",
            "model_version": "hit_gbm_v2",
            "candidate_cohort_id": cohort_id,
            "candidate_count": 18,
        }
        database = FakePicksDatabase(fetch_one_row=existing)

        async def fake_database():
            return database

        monkeypatch.setattr(hit_picks_store, "get_picks_db", fake_database)
        inserted = await replace_picks(
            pick_date="2026-07-05",
            model_version="hit_gbm_v2",
            generated_at="2026-07-05T12:00:00+00:00",
            trained_on_rows=150000,
            candidates=candidates,
            run_id=existing["run_id"],
            candidate_cohort_id=existing["candidate_cohort_id"],
        )

        assert inserted == 18
        assert database.executed == []
        assert database.inserted == []

    @pytest.mark.asyncio
    async def test_grade_write_includes_full_statline(self, monkeypatch):
        database = FakePicksDatabase(
            rows=[{"id": 10, "game_pk": 701, "player_id": 42}]
        )

        async def fake_database():
            return database

        monkeypatch.setattr(hit_picks_store, "get_picks_db", fake_database)
        updated = await apply_grades(
            pick_date="2026-07-05",
            outcomes={
                (701, 42): {
                    "hits": 2,
                    "at_bats": 4,
                    "plate_appearances": 5,
                    "doubles": 1,
                    "triples": 0,
                    "home_runs": 1,
                    "runs": 2,
                    "rbi": 3,
                    "walks": 1,
                    "strikeouts": 1,
                    "total_bases": 6,
                }
            },
        )

        assert updated == 1
        params = database.executed[0].compile().params
        assert params["at_bats"] == 4
        assert params["home_runs"] == 1
        assert params["total_bases"] == 6

    @pytest.mark.asyncio
    async def test_grading_waits_for_each_specific_game(self, monkeypatch):
        database = FakePicksDatabase(
            rows=[
                {"id": 10, "game_pk": 701, "player_id": 42},
                {"id": 11, "game_pk": 702, "player_id": 42},
            ]
        )

        async def fake_database():
            return database

        monkeypatch.setattr(hit_picks_store, "get_picks_db", fake_database)
        updated = await apply_grades(
            pick_date="2026-07-05",
            outcomes={(701, 42): {"hits": 0}},
        )

        assert updated == 1
        params = database.executed[0].compile().params
        assert params["got_hit"] == 0


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestHitPicksRoutes:
    def test_latest_returns_store_payload(self, client, monkeypatch):
        payload = {
            "date": "2026-07-05", "generated_at": "2026-07-05T12:00:00+00:00",
            "model_version": "hit_gbm_v2", "trained_on_rows": 149241,
            "picks": [{"player_id": 1, "player_name": "A", "rank": 1}],
        }

        async def fake_fetch(*, top):
            assert top == 5
            return payload

        monkeypatch.setattr(hit_picks_store, "fetch_latest_picks", fake_fetch)
        response = client.get("/hit-picks/latest?top=5")
        assert response.status_code == 200
        assert response.json()["data"] == payload

    def test_latest_404_when_no_picks_stored(self, client, monkeypatch):
        async def fake_fetch(*, top):
            return None

        monkeypatch.setattr(hit_picks_store, "fetch_latest_picks", fake_fetch)
        assert client.get("/hit-picks/latest").status_code == 404

    def test_ledger_returns_summary(self, client, monkeypatch):
        async def fake_ledger():
            return {"summary": {"hit_gbm_v2": {"days": 1}}, "days_graded": 1}

        monkeypatch.setattr(hit_picks_store, "fetch_ledger_summary", fake_ledger)
        response = client.get("/hit-picks/ledger")
        assert response.status_code == 200
        assert response.json()["data"]["days_graded"] == 1

    def test_ledger_404_when_nothing_graded(self, client, monkeypatch):
        async def fake_ledger():
            return {"summary": {}, "days_graded": 0}

        monkeypatch.setattr(hit_picks_store, "fetch_ledger_summary", fake_ledger)
        assert client.get("/hit-picks/ledger").status_code == 404

    def test_dates_returns_calendar_metadata(self, client, monkeypatch):
        payload = {
            "dates": [{"date": "2026-07-05", "grading_status": "graded"}],
            "latest_date": "2026-07-05",
            "count": 1,
        }

        async def fake_dates(*, limit):
            assert limit == 30
            return payload

        monkeypatch.setattr(hit_picks_store, "fetch_available_dates", fake_dates)
        response = client.get("/hit-picks/dates?limit=30")
        assert response.status_code == 200
        assert response.json()["data"] == payload

    def test_historical_date_returns_store_payload(self, client, monkeypatch):
        payload = {
            "date": "2026-07-05",
            "model_version": "hit_gbm_v2",
            "grading_status": "graded",
            "picks": [{"rank": 1, "hits": 2, "at_bats": 4}],
        }

        async def fake_history(*, pick_date, top, model_version, run_id):
            assert pick_date == "2026-07-05"
            assert top == 10
            assert model_version == "hit_gbm_v2"
            assert run_id is None
            return payload

        monkeypatch.setattr(hit_picks_store, "fetch_picks_for_date", fake_history)
        response = client.get(
            "/hit-picks/2026-07-05?top=10&model_version=hit_gbm_v2"
        )
        assert response.status_code == 200
        assert response.json()["data"] == payload

    def test_historical_run_id_retrieves_exact_snapshot(self, client, monkeypatch):
        run_id = "3e77a1fa-6ff8-4821-aa20-ee51687b483c"

        async def fake_history(*, pick_date, top, model_version, run_id):
            assert pick_date == "2026-07-05"
            assert model_version is None
            assert run_id == "3e77a1fa-6ff8-4821-aa20-ee51687b483c"
            return {"run_id": run_id, "picks": []}

        monkeypatch.setattr(hit_picks_store, "fetch_picks_for_date", fake_history)
        response = client.get(f"/hit-picks/2026-07-05?run_id={run_id}")
        assert response.status_code == 200
        assert response.json()["data"]["run_id"] == run_id

    def test_historical_date_validates_and_404s(self, client, monkeypatch):
        assert client.get("/hit-picks/not-a-date").status_code == 422

        async def fake_history(*, pick_date, top, model_version, run_id):
            return None

        monkeypatch.setattr(hit_picks_store, "fetch_picks_for_date", fake_history)
        response = client.get("/hit-picks/2026-07-05")
        assert response.status_code == 404
        assert "2026-07-05" in response.json()["detail"]
