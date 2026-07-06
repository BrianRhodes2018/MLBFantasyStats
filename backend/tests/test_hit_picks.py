"""Tests for grade_hit_picks.py, hit_picks_store.py, and the /hit-picks routes."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import hit_picks_store
from grade_hit_picks import grade_candidates, summarize_ledger
from hit_picks_store import summarize_pick_rows
from routers.hit_picks import router


def make_candidates():
    # Saved pick lists are sorted by predicted probability descending.
    return [
        {"player_id": 1, "player_name": "A"},
        {"player_id": 2, "player_name": "B"},
        {"player_id": 3, "player_name": "C"},
        {"player_id": 4, "player_name": "D"},
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
