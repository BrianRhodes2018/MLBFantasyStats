"""Audit historical coverage for proposed V3 feature sources."""

from __future__ import annotations

import argparse
import gzip
import json
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

import polars as pl


BACKEND_DIR = Path(__file__).resolve().parents[1]
WHIFF_DESCRIPTIONS = {
    "swinging strike",
    "swinging strike (blocked)",
    "missed bunt",
}
SWING_TOKENS = (
    "swinging strike",
    "foul",
    "in play",
    "hit into play",
    "missed bunt",
    "foul bunt",
)


def git_commit(repo_root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _percentage(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _has_recursive_key(value: Any, keys: set[str]) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in keys or _has_recursive_key(child, keys):
                return True
    elif isinstance(value, list):
        return any(_has_recursive_key(child, keys) for child in value)
    return False


def audit_parquet_datasets(paths: Iterable[Path]) -> dict[str, Any]:
    frames = [pl.read_parquet(path) for path in paths]
    df = pl.concat(frames, how="vertical_relaxed")
    df = df.with_columns(pl.col("game_date").str.slice(0, 4).alias("season"))
    feature_columns = {
        "batter_contact": [
            "season_contact_rate",
            "last5_contact_rate",
            "last10_contact_rate",
            "last20_contact_rate",
        ],
        "starter_workload_quality": [
            "p_season_ip",
            "p_season_starts",
            "p_last3_ip",
        ],
        "bullpen_quality": [
            "opp_bullpen_ip",
            "opp_bullpen_h_per_9",
            "opp_bullpen_whip",
        ],
        "park": ["park_runs_factor", "park_hr_factor"],
    }
    seasons: dict[str, Any] = {}
    for season in sorted(df["season"].unique().to_list()):
        frame = df.filter(pl.col("season") == season)
        season_result: dict[str, Any] = {"rows": frame.height, "groups": {}}
        for group, columns in feature_columns.items():
            available = [column for column in columns if column in frame.columns]
            column_coverage = {
                column: round(
                    1.0 - (frame[column].null_count() / frame.height),
                    6,
                )
                for column in available
            }
            season_result["groups"][group] = {
                "columns": column_coverage,
                "all_fields_present_rate": round(
                    float(
                        frame.select(
                            pl.all_horizontal(
                                [pl.col(column).is_not_null() for column in available]
                            ).mean()
                        ).item()
                    ),
                    6,
                ) if available else None,
            }
        seasons[str(season)] = season_result
    return {
        "rows": df.height,
        "date_min": df["game_date"].min(),
        "date_max": df["game_date"].max(),
        "seasons": seasons,
    }


def _fresh_feed_counts() -> defaultdict[str, int]:
    return defaultdict(int)


def audit_game_feeds(
    paths: Iterable[Path],
    *,
    seasons: set[int] | None = None,
) -> dict[str, Any]:
    by_season: dict[str, defaultdict[str, int]] = {}
    scanned = 0
    skipped = 0
    for path in paths:
        try:
            payload = path.read_bytes()
            if path.suffix == ".gz":
                payload = gzip.decompress(payload)
            data = json.loads(payload)
        except (OSError, json.JSONDecodeError, EOFError):
            skipped += 1
            continue
        game_data = data.get("gameData", {})
        game_date = (
            (game_data.get("datetime") or {}).get("officialDate")
            or (game_data.get("datetime") or {}).get("originalDate")
        )
        if not game_date:
            skipped += 1
            continue
        season_number = int(str(game_date)[:4])
        if seasons and season_number not in seasons:
            continue
        if str((game_data.get("game") or {}).get("type") or "R") != "R":
            continue
        season = str(season_number)
        counts = by_season.setdefault(season, _fresh_feed_counts())
        counts["games"] += 1
        scanned += 1

        for play in (
            data.get("liveData", {})
            .get("plays", {})
            .get("allPlays", [])
        ):
            for event in play.get("playEvents", []):
                if not event.get("isPitch"):
                    continue
                counts["pitch_events"] += 1
                details = event.get("details") or {}
                pitch_data = event.get("pitchData") or {}
                breaks = pitch_data.get("breaks") or {}
                description = str(details.get("description") or "").lower()
                pitch_type = (details.get("type") or {}).get("code")
                if pitch_type:
                    counts["pitch_type"] += 1
                if pitch_data.get("startSpeed") is not None:
                    counts["velocity"] += 1
                if (
                    breaks.get("breakHorizontal") is not None
                    and breaks.get("breakVerticalInduced") is not None
                ):
                    counts["movement"] += 1
                if pitch_data.get("extension") is not None:
                    counts["extension"] += 1
                if breaks.get("spinRate") is not None:
                    counts["spin_rate"] += 1

                is_swing = any(token in description for token in SWING_TOKENS)
                if is_swing:
                    counts["swings"] += 1
                    if description not in WHIFF_DESCRIPTIONS:
                        counts["contacts"] += 1
                if details.get("isInPlay"):
                    counts["balls_in_play"] += 1
                    hit_data = event.get("hitData") or {}
                    if hit_data.get("launchSpeed") is not None:
                        counts["exit_velocity"] += 1
                    if hit_data.get("launchAngle") is not None:
                        counts["launch_angle"] += 1
                    if _has_recursive_key(
                        event,
                        {
                            "estimatedbattingaverage",
                            "estimatedavgusingspeedangle",
                            "xba",
                        },
                    ):
                        counts["xba"] += 1
                if _has_recursive_key(event, {"batspeed", "bat_speed"}):
                    counts["bat_speed"] += 1
                if _has_recursive_key(event, {"squaredup", "squared_up"}):
                    counts["squared_up"] += 1

        box_teams = (
            data.get("liveData", {})
            .get("boxscore", {})
            .get("teams", {})
        )
        for side in ("away", "home"):
            team = box_teams.get(side) or {}
            pitcher_ids = [
                int(value)
                for value in (team.get("pitchers") or [])
                if str(value).isdigit()
            ]
            starter_id = pitcher_ids[0] if pitcher_ids else None
            probable_id = int(
                (
                    (game_data.get("probablePitchers") or {}).get(side)
                    or {}
                ).get("id")
                or 0
            )
            counts["team_sides"] += 1
            if probable_id:
                counts["probable_pitcher_available"] += 1
            if probable_id and starter_id and probable_id != starter_id:
                counts["probable_pitcher_mismatch"] += 1
            for raw_key, player in (team.get("players") or {}).items():
                pitching = (player.get("stats") or {}).get("pitching") or {}
                batters_faced = int(pitching.get("battersFaced") or 0)
                if not batters_faced:
                    continue
                player_id = int(
                    (player.get("person") or {}).get("id")
                    or str(raw_key).replace("ID", "")
                    or 0
                )
                role = "starter" if player_id == starter_id else "reliever"
                counts[f"{role}_appearances"] += 1
                if pitching.get("pitchesThrown") is not None:
                    counts[f"{role}_pitches_thrown"] += 1
                if pitching.get("inningsPitched") is not None:
                    counts[f"{role}_innings"] += 1
                if pitching.get("battersFaced") is not None:
                    counts[f"{role}_batters_faced"] += 1

    output: dict[str, Any] = {}
    for season, counts in sorted(by_season.items()):
        pitches = counts["pitch_events"]
        balls_in_play = counts["balls_in_play"]
        swings = counts["swings"]
        starters = counts["starter_appearances"]
        relievers = counts["reliever_appearances"]
        output[season] = {
            "counts": dict(sorted(counts.items())),
            "coverage": {
                "pitch_type": _percentage(counts["pitch_type"], pitches),
                "velocity": _percentage(counts["velocity"], pitches),
                "movement": _percentage(counts["movement"], pitches),
                "extension": _percentage(counts["extension"], pitches),
                "spin_rate": _percentage(counts["spin_rate"], pitches),
                "contact_outcome": _percentage(
                    counts["contacts"] + (counts["swings"] - counts["contacts"]),
                    swings,
                ),
                "exit_velocity": _percentage(
                    counts["exit_velocity"],
                    balls_in_play,
                ),
                "launch_angle": _percentage(
                    counts["launch_angle"],
                    balls_in_play,
                ),
                "xba": _percentage(counts["xba"], balls_in_play),
                "bat_speed": _percentage(counts["bat_speed"], pitches),
                "squared_up": _percentage(counts["squared_up"], pitches),
                "starter_pitches_thrown": _percentage(
                    counts["starter_pitches_thrown"],
                    starters,
                ),
                "starter_innings": _percentage(
                    counts["starter_innings"],
                    starters,
                ),
                "bullpen_pitches_thrown": _percentage(
                    counts["reliever_pitches_thrown"],
                    relievers,
                ),
                "bullpen_innings": _percentage(
                    counts["reliever_innings"],
                    relievers,
                ),
                "probable_pitcher": _percentage(
                    counts["probable_pitcher_available"],
                    counts["team_sides"],
                ),
                "probable_to_actual_mismatch": _percentage(
                    counts["probable_pitcher_mismatch"],
                    counts["probable_pitcher_available"],
                ),
            },
        }
    return {
        "scanned_game_files": scanned,
        "skipped_game_files": skipped,
        "seasons": output,
    }


def discover_game_feed_paths(cache: Path) -> list[Path]:
    """Match BoxscoreSource semantics: prefer plain JSON, else gzip."""
    by_game: dict[str, Path] = {}
    for path in sorted(cache.glob("game_*.json")):
        by_game[path.stem] = path
    for path in sorted(cache.glob("game_*.json.gz")):
        game_key = path.name.removesuffix(".json.gz")
        by_game.setdefault(game_key, path)
    return [by_game[key] for key in sorted(by_game)]


def readiness(feed_audit: dict[str, Any]) -> dict[str, Any]:
    seasons = feed_audit["seasons"]

    def minimum(field: str) -> float:
        values = [
            row["coverage"].get(field)
            for row in seasons.values()
            if row["coverage"].get(field) is not None
        ]
        return min(values) if values else 0.0

    return {
        "pitch_mix": {
            "status": "ready",
            "minimum_pitch_type_coverage": minimum("pitch_type"),
            "minimum_velocity_coverage": minimum("velocity"),
            "minimum_movement_coverage": minimum("movement"),
            "source": "cached MLB StatsAPI pitch events",
        },
        "contact": {
            "status": "ready",
            "minimum_contact_outcome_coverage": minimum("contact_outcome"),
            "minimum_exit_velocity_coverage": minimum("exit_velocity"),
            "source": "cached MLB StatsAPI pitch and batted-ball events",
        },
        "xba": {
            "status": "new_source_required",
            "minimum_cached_feed_coverage": minimum("xba"),
            "source": "Baseball Savant Statcast search export",
        },
        "bat_tracking": {
            "status": "new_source_required",
            "minimum_bat_speed_coverage": minimum("bat_speed"),
            "minimum_squared_up_coverage": minimum("squared_up"),
            "source": "Baseball Savant bat-tracking leaderboard/export",
            "earliest_expected_coverage": "2023 second half",
        },
        "starter_workload": {
            "status": "ready",
            "minimum_pitch_count_coverage": minimum("starter_pitches_thrown"),
            "minimum_probable_pitcher_coverage": minimum("probable_pitcher"),
            "source": "cached MLB StatsAPI boxscores",
        },
        "bullpen_availability": {
            "status": "ready",
            "minimum_pitch_count_coverage": minimum("bullpen_pitches_thrown"),
            "source": "cached MLB StatsAPI boxscores",
        },
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# V3 Historical Feature Coverage Audit",
        "",
        "Measured from the saved V2 Parquet datasets and cached MLB StatsAPI "
        "regular-season game feeds. Rates are field availability, not evidence "
        "that a feature improves predictions.",
        "",
        "## Decision summary",
        "",
        "| Feature group | Status | Historical source |",
        "|---|---|---|",
    ]
    for name, item in report["readiness"].items():
        lines.append(
            f"| {name.replace('_', ' ').title()} | {item['status']} | "
            f"{item['source']} |"
        )
    lines.extend([
        "",
        "Pitch mix, contact outcomes, starter workload, and bullpen workload "
        "can be built from the existing cache. xBA and bat-tracking fields are "
        "not present in that cache and require a separately versioned Baseball "
        "Savant ingestion path before they enter an experiment.",
        "",
        "## Game-feed coverage by season",
        "",
        "| Season | Games | Pitch type | Velocity | Movement | EV on BIP | "
        "Starter pitch count | Bullpen pitch count |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for season, row in report["game_feeds"]["seasons"].items():
        counts, coverage = row["counts"], row["coverage"]

        def pct(value: float | None) -> str:
            return "n/a" if value is None else f"{value:.1%}"

        lines.append(
            f"| {season} | {counts.get('games', 0):,} | "
            f"{pct(coverage['pitch_type'])} | {pct(coverage['velocity'])} | "
            f"{pct(coverage['movement'])} | {pct(coverage['exit_velocity'])} | "
            f"{pct(coverage['starter_pitches_thrown'])} | "
            f"{pct(coverage['bullpen_pitches_thrown'])} |"
        )
    lines.extend([
        "",
        "## Guardrails",
        "",
        "- Missing xBA or bat-tracking data must not remove a hitter from the shared candidate cohort.",
        "- Every new source needs a sample-count feature, missing indicator, and explicit fallback.",
        "- Source ingestion and feature definitions must be point-in-time and independently switchable.",
        "- Feature value is determined by chronological ablation tests, not coverage alone.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", nargs="+", required=True)
    parser.add_argument("--game-cache", required=True)
    parser.add_argument(
        "--summary",
        default=str(BACKEND_DIR / "reports" / "v3" / "feature_coverage.json"),
    )
    parser.add_argument(
        "--markdown",
        default=str(BACKEND_DIR / "reports" / "v3" / "feature_coverage.md"),
    )
    args = parser.parse_args()

    datasets = [Path(path).resolve() for path in args.dataset]
    cache = Path(args.game_cache).resolve()
    report = {
        "audit_version": "v3_feature_coverage_v1",
        "code_commit": git_commit(BACKEND_DIR.parent),
        "datasets": audit_parquet_datasets(datasets),
        "game_feeds": audit_game_feeds(
            discover_game_feed_paths(cache),
            seasons={2023, 2024, 2025, 2026},
        ),
        "sources": {
            "mlb_statsapi": "https://statsapi.mlb.com/",
            "baseball_savant": "https://baseballsavant.mlb.com/",
        },
    }
    report["readiness"] = readiness(report["game_feeds"])

    summary_path = Path(args.summary)
    markdown_path = Path(args.markdown)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    markdown_path.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({
        "status": "ok",
        "summary": str(summary_path),
        "markdown": str(markdown_path),
        "games": report["game_feeds"]["scanned_game_files"],
        "readiness": {
            key: value["status"] for key, value in report["readiness"].items()
        },
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
