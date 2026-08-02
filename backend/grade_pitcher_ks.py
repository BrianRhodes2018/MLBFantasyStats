"""Confirm published Pitcher Ks projections from official final MLB feeds.

The command is read-only by default. Pass ``--apply`` only after reviewing
the proposed status counts. Re-running is idempotent; conflicting final
strikeout totals require an explicit ``--force`` for audited stat corrections.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional

from build_hit_dataset import DEFAULT_CACHE_DIR, BoxscoreSource, safe_int
from database import database
from pitcher_ks.store import apply_grades, fetch_prediction_identities


GRADING_SOURCE = "MLB Stats API final game feed"
FINAL_DETAILED_STATES = {
    "final",
    "game over",
    "completed early",
    "completed early: rain",
}


def _game_status(feed: Optional[Mapping[str, Any]]) -> tuple[str, str]:
    if not feed:
        return "data_unavailable", "MLB game feed was unavailable."
    status = (feed.get("gameData") or {}).get("status") or {}
    abstract = str(status.get("abstractGameState") or "").strip()
    detailed = str(status.get("detailedState") or abstract or "Unknown").strip()
    lowered = detailed.lower()
    if abstract.lower() == "final" or lowered in FINAL_DETAILED_STATES:
        return "final", detailed
    if "postpon" in lowered:
        return "postponed", detailed
    if "suspend" in lowered or "delay" in lowered:
        return "suspended", detailed
    if "cancel" in lowered or "forfeit" in lowered:
        return "cancelled", detailed
    return "pending", detailed


def _pitching_box(feed: Mapping[str, Any], pitcher_id: int) -> Optional[Mapping[str, Any]]:
    teams = (feed.get("liveData") or {}).get("boxscore", {}).get("teams") or {}
    for side in ("away", "home"):
        players = (teams.get(side) or {}).get("players") or {}
        box = players.get(f"ID{pitcher_id}")
        if box is None:
            box = next(
                (
                    candidate
                    for candidate in players.values()
                    if safe_int((candidate.get("person") or {}).get("id")) == pitcher_id
                ),
                None,
            )
        if box is not None:
            return (box.get("stats") or {}).get("pitching") or {}
    return None


def grade_from_feed(
    *,
    game_pk: int,
    pitcher_id: int,
    feed: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    """Shape one pitcher-game result without performing database writes."""
    game_state, detailed_state = _game_status(feed)
    common = {
        "game_status": detailed_state,
        "grading_source": GRADING_SOURCE,
    }
    if game_state != "final":
        detail_by_status = {
            "pending": "Game has not reached an official final state.",
            "postponed": "Game was postponed; the projection remains unresolved.",
            "suspended": "Game is delayed or suspended; grading will retry later.",
            "cancelled": "Game was cancelled or forfeited without a starter result.",
            "data_unavailable": "Official game data could not be retrieved.",
        }
        return {
            **common,
            "result_status": game_state,
            "started": 0 if game_state == "cancelled" else None,
            "grade_detail": detail_by_status[game_state],
        }

    pitching = _pitching_box(feed or {}, pitcher_id)
    if pitching is None or safe_int(pitching.get("gamesStarted")) < 1:
        return {
            **common,
            "result_status": "did_not_start",
            "started": 0,
            "grade_detail": "Projected probable pitcher did not start the specified game.",
        }

    outs = safe_int(pitching.get("outs"))
    return {
        **common,
        "result_status": "graded",
        "started": 1,
        "actual_ks": safe_int(pitching.get("strikeOuts")),
        "actual_batters_faced": safe_int(pitching.get("battersFaced")),
        "actual_innings_pitched": round(outs / 3.0, 3),
        "actual_pitch_count": safe_int(
            pitching.get("pitchesThrown") or pitching.get("numberOfPitches")
        ),
        "grade_detail": "Confirmed from the official final MLB pitching line.",
    }


def outcomes_for_predictions(
    source: BoxscoreSource,
    predictions: list[Mapping[str, Any]],
) -> dict[tuple[int, int], dict[str, Any]]:
    feeds: dict[int, Optional[Mapping[str, Any]]] = {}
    outcomes: dict[tuple[int, int], dict[str, Any]] = {}
    for prediction in predictions:
        game_pk = int(prediction["game_pk"])
        pitcher_id = int(prediction["pitcher_id"])
        if game_pk not in feeds:
            feeds[game_pk] = source.game(game_pk, refresh=True)
        outcomes[(game_pk, pitcher_id)] = grade_from_feed(
            game_pk=game_pk,
            pitcher_id=pitcher_id,
            feed=feeds[game_pk],
        )
    return outcomes


async def run_grading(
    *,
    target: date,
    source: BoxscoreSource,
    apply: bool,
    force: bool,
) -> dict[str, Any]:
    await database.connect()
    try:
        identities = await fetch_prediction_identities(target.isoformat())
        if not identities:
            raise RuntimeError(f"No Pitcher Ks projections exist for {target.isoformat()}.")
        outcomes = outcomes_for_predictions(source, identities)
        return await apply_grades(
            projection_date=target.isoformat(),
            outcomes=outcomes,
            dry_run=not apply,
            force=force,
        )
    finally:
        await database.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        dest="target_date",
        help="Projection date in YYYY-MM-DD format; defaults to yesterday.",
    )
    parser.add_argument(
        "--cache-dir",
        default=str(DEFAULT_CACHE_DIR / "pitcher_ks_grading"),
        help="Cache directory for refreshed MLB game feeds.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write reviewed outcomes. Without this flag the command is a dry run.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow an official stat correction to replace a conflicting final result.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.force and not args.apply:
        raise SystemExit("--force requires --apply.")
    target = (
        date.fromisoformat(args.target_date)
        if args.target_date
        else date.today() - timedelta(days=1)
    )
    source = BoxscoreSource(Path(args.cache_dir))
    result = asyncio.run(
        run_grading(
            target=target,
            source=source,
            apply=bool(args.apply),
            force=bool(args.force),
        )
    )
    mode = "APPLIED" if args.apply else "DRY RUN"
    print(f"Pitcher Ks grading {mode}: {json.dumps(result, sort_keys=True)}")
    if not args.apply:
        print("Review the counts, then rerun with --apply to persist them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
