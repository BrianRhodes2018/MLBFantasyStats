"""
grade_hit_picks.py - Grade saved daily pick lists against actual outcomes.

Every run of predict_hits_today.py saves hit_picks_YYYY-MM-DD.json. This
script closes the loop: for each pick file whose games have finished, it
looks up the real boxscores and records how the top-5/10/15 picks did,
maintaining a running ledger keyed by date. Because every pick file
carries a model_version stamp, the ledger accumulates a live hit-rate
track record PER MODEL GENERATION — the honest scoreboard for whether a
new version actually beats the old one.

Re-running is safe: each date's entry is simply recomputed and replaced.

Example:
    python backend/grade_hit_picks.py
    python backend/grade_hit_picks.py --picks-dir backend/backtest_results
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional

from build_hit_dataset import BACKEND_DIR, DEFAULT_CACHE_DIR, BoxscoreSource, safe_int
from hit_picks_store import apply_grades, close_picks_db


async def write_grades_to_db(
    graded_outcomes: list[tuple[str, dict[tuple[int, int], dict[str, int]]]],
) -> None:
    """Fill the grading columns on the stored hit_picks rows (in the
    production picks database — see hit_picks_store module doc)."""
    try:
        for pick_date, outcomes in graded_outcomes:
            updated = await apply_grades(pick_date=pick_date, outcomes=outcomes)
            print(f"{pick_date}: graded {updated} stored picks in the picks database.")
    finally:
        await close_picks_db()

DEFAULT_PICKS_DIR = BACKEND_DIR / "backtest_results"
DEFAULT_LEDGER = BACKEND_DIR / "data" / "hit_picks_ledger.json"
TOP_NS = (5, 10, 15)

_PICK_FILE_RE = re.compile(r"hit_picks_(\d{4}-\d{2}-\d{2})\.json$")


class GameOutcomes(dict[tuple[int, int], dict[str, int]]):
    """Statlines plus final-game membership needed for safe legacy handling."""

    def __init__(self) -> None:
        super().__init__()
        self.final_game_pks: set[int] = set()
        self.player_game_pks: dict[int, set[int]] = {}


def _game_pk(slate_game: Mapping[str, Any]) -> int:
    game = slate_game.get("game") or {}
    return safe_int(
        (slate_game.get("schedule") or {}).get("game_id")
        or game.get("gamePk")
        or ((game.get("gameData") or {}).get("game") or {}).get("pk")
    )


def outcomes_for_date(
    source: BoxscoreSource,
    target: date,
) -> GameOutcomes:
    """(game_pk, player_id) -> batting line for each batter who appeared.

    The schedule is force-refreshed: grading runs the morning after, and
    the cached copy may have been written earlier that day while games
    were still scheduled/in progress (which would read as "no final
    games" forever). One extra API call per newly graded date.
    """
    outcomes = GameOutcomes()
    for slate_game in source.final_games(target, refresh_schedule=True):
        game_pk = _game_pk(slate_game)
        if not game_pk:
            continue
        outcomes.final_game_pks.add(game_pk)
        teams = slate_game["game"].get("liveData", {}).get("boxscore", {}).get("teams", {})
        for side in ("away", "home"):
            for box_player in (teams.get(side, {}).get("players") or {}).values():
                pid = safe_int((box_player.get("person") or {}).get("id"))
                if pid:
                    outcomes.player_game_pks.setdefault(pid, set()).add(game_pk)
                batting = box_player.get("stats", {}).get("batting") or {}
                pa = safe_int(batting.get("plateAppearances"))
                if pid and pa > 0:
                    outcomes[(game_pk, pid)] = {
                        "hits": safe_int(batting.get("hits")),
                        "at_bats": safe_int(batting.get("atBats")),
                        "plate_appearances": pa,
                        "doubles": safe_int(batting.get("doubles")),
                        "triples": safe_int(batting.get("triples")),
                        "home_runs": safe_int(batting.get("homeRuns")),
                        "runs": safe_int(batting.get("runs")),
                        "rbi": safe_int(batting.get("rbi")),
                        "walks": safe_int(batting.get("baseOnBalls")),
                        "strikeouts": safe_int(batting.get("strikeOuts")),
                        "total_bases": safe_int(batting.get("totalBases")),
                    }
    return outcomes


def _candidate_outcome(
    candidate: Mapping[str, Any],
    outcomes: Mapping[Any, Mapping[str, int]],
) -> Optional[Mapping[str, int]]:
    player_id = safe_int(candidate.get("player_id"))
    game_pk = safe_int(candidate.get("game_pk") or candidate.get("game_id"))
    if game_pk and (game_pk, player_id) in outcomes:
        return outcomes[(game_pk, player_id)]
    player_game_pks = getattr(outcomes, "player_game_pks", {}).get(player_id, set())
    if not game_pk and len(player_game_pks) > 1:
        return None
    if player_id in outcomes:  # Legacy player-only input.
        return outcomes[player_id]
    matches = [
        outcome
        for key, outcome in outcomes.items()
        if isinstance(key, tuple)
        and len(key) == 2
        and safe_int(key[1]) == player_id
    ]
    # A game-less legacy pick can be matched only when the player appeared
    # in exactly one final game. Never combine or guess across a doubleheader.
    return matches[0] if len(matches) == 1 else None


def grade_candidates(
    candidates: list[Mapping[str, Any]],
    outcomes: Mapping[Any, Mapping[str, int]],
    top_ns: tuple[int, ...] = TOP_NS,
) -> dict[str, dict[str, int]]:
    """
    For each N, take the first N candidates (pick files are saved sorted
    by predicted probability) and count how many actually played and how
    many of those got a hit. Players who didn't play (scratched, lineup
    projection missed) are excluded from the denominator but reported.
    """
    grades = {}
    for n in top_ns:
        top = candidates[:n]
        played = [
            (candidate, _candidate_outcome(candidate, outcomes))
            for candidate in top
        ]
        played = [(candidate, outcome) for candidate, outcome in played if outcome]
        hits = sum(1 for _, outcome in played if outcome["hits"] >= 1)
        grades[f"top{n}"] = {"picks": len(top), "played": len(played), "hits": hits}
    return grades


def load_ledger(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"entries": {}}


def summarize_ledger(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Aggregate the per-day entries into per-model-version hit rates."""
    by_version: dict[str, dict[str, Any]] = {}
    for entry in ledger.get("entries", {}).values():
        version = entry.get("model_version") or "unknown"
        agg = by_version.setdefault(
            version,
            {"days": 0, **{f"top{n}": {"played": 0, "hits": 0} for n in TOP_NS}},
        )
        agg["days"] += 1
        for n in TOP_NS:
            grade = entry.get("grades", {}).get(f"top{n}")
            if grade:
                agg[f"top{n}"]["played"] += grade["played"]
                agg[f"top{n}"]["hits"] += grade["hits"]

    for agg in by_version.values():
        for n in TOP_NS:
            bucket = agg[f"top{n}"]
            bucket["hit_rate"] = (
                round(bucket["hits"] / bucket["played"], 4) if bucket["played"] else None
            )
    return by_version


def main() -> int:
    parser = argparse.ArgumentParser(description="Grade saved hit pick lists against boxscores.")
    parser.add_argument("--picks-dir", default=str(DEFAULT_PICKS_DIR), help="Directory containing hit_picks_*.json.")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER), help="Ledger JSON path (created if missing).")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="Shared MLB StatsAPI JSON cache directory.")
    parser.add_argument("--regrade", action="store_true", help="Re-grade dates already in the ledger (normally skipped).")
    args = parser.parse_args()

    source = BoxscoreSource(Path(args.cache_dir))
    ledger_path = Path(args.ledger)
    ledger = load_ledger(ledger_path)

    pick_files = sorted(Path(args.picks_dir).glob("hit_picks_*.json"))
    if not pick_files:
        print(f"No hit_picks_*.json files found in {args.picks_dir}.")
        return 1

    graded = skipped = 0
    graded_outcomes: list[
        tuple[str, dict[tuple[int, int], dict[str, int]]]
    ] = []
    for pick_file in pick_files:
        match = _PICK_FILE_RE.search(pick_file.name)
        if not match:
            continue
        pick_date = date.fromisoformat(match.group(1))
        if pick_date.isoformat() in ledger["entries"] and not args.regrade:
            continue  # already graded; --regrade forces a redo
        if pick_date >= date.today():
            print(f"{pick_date}: games not finished — will grade tomorrow.")
            skipped += 1
            continue
        picks = json.loads(pick_file.read_text(encoding="utf-8"))
        outcomes = outcomes_for_date(source, pick_date)
        if not outcomes:
            print(f"{pick_date}: no final games yet — skipping.")
            skipped += 1
            continue
        grades = grade_candidates(picks.get("candidates", []), outcomes)
        ledger["entries"][pick_date.isoformat()] = {
            "date": pick_date.isoformat(),
            "model_version": picks.get("model_version")
            or ("hit_logistic_v1" if "logistic" in str(picks.get("model", "")) else "unknown"),
            "grades": grades,
        }
        top10 = grades["top10"]
        rate = f"{top10['hits']}/{top10['played']}" if top10["played"] else "0 played"
        print(f"{pick_date}: graded (top-10: {rate}).")
        graded += 1
        graded_outcomes.append((pick_date.isoformat(), outcomes))

    ledger["summary"] = summarize_ledger(ledger)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")

    # Mirror the grades into the shared database so the deployed backend's
    # /hit-picks/ledger reflects them too. The file ledger above remains the
    # local source of truth if the DB is unreachable.
    if graded_outcomes:
        try:
            asyncio.run(write_grades_to_db(graded_outcomes))
        except Exception as exc:
            print(f"Warning: could not write grades to the database: {exc}")

    print(f"\nLEDGER — {graded} graded, {skipped} pending")
    print(f"{'model version':18s} {'days':>4s} {'top5':>12s} {'top10':>12s} {'top15':>12s}")
    for version, agg in sorted(ledger["summary"].items()):
        cells = []
        for n in TOP_NS:
            bucket = agg[f"top{n}"]
            if bucket["played"]:
                cells.append(f"{bucket['hits']}/{bucket['played']} ({bucket['hit_rate'] * 100:.0f}%)")
            else:
                cells.append("-")
        print(f"{version:18s} {agg['days']:>4d} {cells[0]:>12s} {cells[1]:>12s} {cells[2]:>12s}")
    print(f"\nSaved ledger: {ledger_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
