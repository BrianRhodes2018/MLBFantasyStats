import gzip
import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_v3_feature_coverage.py"
SPEC = importlib.util.spec_from_file_location("audit_v3_feature_coverage", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def test_game_feed_audit_counts_pitch_and_workload_fields(tmp_path):
    payload = {
        "gameData": {
            "datetime": {"officialDate": "2025-06-01"},
            "game": {"type": "R"},
        },
        "liveData": {
            "plays": {
                "allPlays": [{
                    "playEvents": [{
                        "isPitch": True,
                        "details": {
                            "description": "In play, no out",
                            "isInPlay": True,
                            "type": {"code": "FF"},
                        },
                        "pitchData": {
                            "startSpeed": 95.1,
                            "extension": 6.2,
                            "breaks": {
                                "breakHorizontal": 8.0,
                                "breakVerticalInduced": 15.0,
                                "spinRate": 2300,
                            },
                        },
                        "hitData": {
                            "launchSpeed": 101.0,
                            "launchAngle": 12.0,
                        },
                    }],
                }],
            },
            "boxscore": {
                "teams": {
                    "away": {
                        "pitchers": [10, 11],
                        "players": {
                            "ID10": {
                                "person": {"id": 10},
                                "stats": {"pitching": {
                                    "battersFaced": 20,
                                    "pitchesThrown": 80,
                                    "inningsPitched": "5.0",
                                }},
                            },
                            "ID11": {
                                "person": {"id": 11},
                                "stats": {"pitching": {
                                    "battersFaced": 5,
                                    "pitchesThrown": 20,
                                    "inningsPitched": "1.0",
                                }},
                            },
                        },
                    },
                    "home": {"pitchers": [], "players": {}},
                },
            },
        },
    }
    path = tmp_path / "game_1.json.gz"
    path.write_bytes(gzip.compress(json.dumps(payload).encode("utf-8")))
    report = audit.audit_game_feeds([path], seasons={2025})
    season = report["seasons"]["2025"]
    assert season["coverage"]["pitch_type"] == 1.0
    assert season["coverage"]["movement"] == 1.0
    assert season["coverage"]["exit_velocity"] == 1.0
    assert season["coverage"]["starter_pitches_thrown"] == 1.0
    assert season["coverage"]["bullpen_pitches_thrown"] == 1.0
    assert season["coverage"]["xba"] == 0.0


def test_cache_discovery_prefers_plain_json_without_duplicates(tmp_path):
    (tmp_path / "game_1.json").write_text("{}", encoding="utf-8")
    (tmp_path / "game_1.json.gz").write_bytes(gzip.compress(b"{}"))
    (tmp_path / "game_2.json.gz").write_bytes(gzip.compress(b"{}"))
    paths = audit.discover_game_feed_paths(tmp_path)
    assert [path.name for path in paths] == ["game_1.json", "game_2.json.gz"]
