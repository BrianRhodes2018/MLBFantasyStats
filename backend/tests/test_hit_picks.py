"""Tests for grade_hit_picks.py and the /hit-picks API routes."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from grade_hit_picks import grade_candidates, summarize_ledger
from routers.hit_picks import router


def make_candidates():
    # Saved pick files are sorted by predicted probability descending.
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


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HIT_PICKS_DIR", str(tmp_path))
    monkeypatch.setenv("HIT_PICKS_LEDGER", str(tmp_path / "ledger.json"))
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), tmp_path


class TestHitPicksRoutes:
    def write_picks(self, tmp_path, date_str, version="hit_gbm_v2"):
        payload = {
            "date": date_str,
            "generated_at": f"{date_str}T12:00:00+00:00",
            "model_version": version,
            "trained_on_rows": 1000,
            "candidates": [
                {"player_id": i, "player_name": f"P{i}", "hit_probability": 0.8 - i * 0.01,
                 "team": "T", "opponent": "O", "venue": "V", "batting_order": 1,
                 "bats": "R", "pitcher_name": "SP", "pitcher_throws": "R",
                 "lineup_source": "test", "season_hit_per_pa": 0.25,
                 "last10_hit_per_pa": 0.3, "platoon_advantage": 1}
                for i in range(20)
            ],
        }
        (tmp_path / f"hit_picks_{date_str}.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_latest_returns_most_recent_trimmed(self, client):
        test_client, tmp_path = client
        self.write_picks(tmp_path, "2026-07-04")
        self.write_picks(tmp_path, "2026-07-05")
        response = test_client.get("/hit-picks/latest?top=5")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["date"] == "2026-07-05"
        assert data["model_version"] == "hit_gbm_v2"
        assert len(data["picks"]) == 5
        # Internal model features must not leak into the public payload.
        assert "p_season_whip" not in data["picks"][0]

    def test_latest_404_when_no_files(self, client):
        test_client, _ = client
        assert test_client.get("/hit-picks/latest").status_code == 404

    def test_ledger_roundtrip(self, client):
        test_client, tmp_path = client
        (tmp_path / "ledger.json").write_text(json.dumps({
            "entries": {"2026-07-04": {"model_version": "hit_gbm_v2", "grades": {}}},
            "summary": {"hit_gbm_v2": {"days": 1}},
        }), encoding="utf-8")
        response = test_client.get("/hit-picks/ledger")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["days_graded"] == 1
        assert "hit_gbm_v2" in data["summary"]

    def test_ledger_404_when_missing(self, client):
        test_client, _ = client
        assert test_client.get("/hit-picks/ledger").status_code == 404
