"""
hit_picks_store.py - Database persistence for daily hit-model picks.

Why this exists: predict_hits_today.py and grade_hit_picks.py run on the
machine that has the boxscore cache and training data, but the deployed
backend (Render) needs to SERVE the picks. This module is the bridge:
the scripts write picks and grades into the shared Postgres database,
and the /hit-picks API routes read them back out — same pattern as the
daily stats update.

Layout: `hit_pick_runs` stores one immutable prediction snapshot and
`hit_picks` stores its ranked rows. Publishing a newer run only moves a
pointer; it never deletes the previous prediction. One evaluation run per
date/model/window feeds the live ledger, so operational reruns cannot
double-count or make morning and afternoon snapshots overwrite each other.
Grading columns start NULL and get filled once that pick's exact game is final.

Which database? The picks live in the PRODUCTION database — the one the
deployed backend reads. Resolution order:
    1. PROD_DATABASE_URL  — set this in backend/.env on the dev machine,
       where DATABASE_URL points at the local Postgres. The scripts then
       write picks where the deployed app can see them.
    2. DATABASE_URL       — the fallback. On Render this IS the
       production database, so no extra variable is needed there.

Backfill existing local JSON pick files into the database:
    python backend/hit_picks_store.py --backfill
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from databases import Database
from dotenv import load_dotenv

from database import normalize_database_url
from hit_model.cohort import freeze_candidate_cohort
from models import hit_pick_runs, hit_picks

BACKEND_DIR = Path(__file__).resolve().parent

# Persist the complete scored slate. Reader endpoints still default to 15,
# but evaluation, rank movement, and entering/leaving-top-N analysis require
# every candidate rather than only the displayed board.
STORED_PICKS_PER_DAY: Optional[int] = None

PREDICTION_WINDOWS = {"morning", "afternoon", "legacy"}
MODEL_ROLES = {"primary", "challenger", "archive"}
PROBABILITY_STATUSES = {
    "calibrated",
    "experimental",
    "uncalibrated",
    "legacy_unknown",
}

TOP_NS = (5, 10, 15)

# Candidate-dict keys copied straight into same-named table columns.
_CANDIDATE_COLUMNS = [
    "game_pk", "player_id", "player_name", "team", "opponent", "venue",
    "batting_order", "bats", "pitcher_id", "pitcher_name", "pitcher_throws",
    "lineup_source", "hit_probability", "season_hit_per_pa",
    "last10_hit_per_pa", "platoon_advantage",
]

_STATLINE_COLUMNS = [
    "hits", "at_bats", "plate_appearances", "doubles", "triples",
    "home_runs", "runs", "rbi", "walks", "strikeouts", "total_bases",
]

_PICK_FILE_RE = re.compile(r"hit_picks_(\d{4}-\d{2}-\d{2})\.json$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Picks database connection (lazy singleton)
# ---------------------------------------------------------------------------

_picks_db: Optional[Database] = None


def picks_database_url() -> str:
    """The connection string for wherever picks are stored (see module doc)."""
    load_dotenv(BACKEND_DIR / ".env")
    raw_url = os.environ.get("PROD_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not raw_url:
        raise RuntimeError(
            "Neither PROD_DATABASE_URL nor DATABASE_URL is set. "
            "Add one to backend/.env or the environment."
        )
    async_url, _ = normalize_database_url(raw_url)
    return async_url


async def get_picks_db() -> Database:
    """Connect (once) to the picks database and reuse the pool after."""
    global _picks_db
    if _picks_db is None:
        _picks_db = Database(picks_database_url())
    if not _picks_db.is_connected:
        await _picks_db.connect()
    return _picks_db


async def close_picks_db() -> None:
    """Disconnect the singleton (scripts call this before exiting)."""
    global _picks_db
    if _picks_db is not None and _picks_db.is_connected:
        await _picks_db.disconnect()
    _picks_db = None


# ---------------------------------------------------------------------------
# Writes (called by predict_hits_today.py / grade_hit_picks.py)
# ---------------------------------------------------------------------------

async def replace_picks(
    *,
    pick_date: str,
    model_version: str,
    generated_at: Optional[str],
    trained_on_rows: Optional[int],
    candidates: list[Mapping[str, Any]],
    top: Optional[int] = STORED_PICKS_PER_DAY,
    is_public: bool = True,
    is_evaluation: bool = True,
    run_id: Optional[str] = None,
    as_of_timestamp: Optional[str] = None,
    prediction_mode: str = "legacy_unknown",
    candidate_cohort_id: Optional[str] = None,
    candidate_manifest: Optional[Mapping[str, Any]] = None,
    runtime_manifest: Optional[Mapping[str, Any]] = None,
    comparison_group_id: Optional[str] = None,
    prediction_window: str = "legacy",
    model_role: Optional[str] = None,
    is_visible: bool = True,
    probability_status: str = "legacy_unknown",
) -> int:
    """Append an immutable run and optionally make it public/evaluated.

    The historical function name remains for scheduler/backfill compatibility.
    No prediction rows are replaced or deleted.
    """
    db = await get_picks_db()
    generated_at = generated_at or _utc_now()
    run_id = run_id or str(uuid4())
    try:
        run_id = str(UUID(run_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("run_id must be a UUID string.") from exc
    if comparison_group_id is not None:
        try:
            comparison_group_id = str(UUID(comparison_group_id))
        except (TypeError, ValueError) as exc:
            raise ValueError("comparison_group_id must be a UUID string.") from exc
    if prediction_window not in PREDICTION_WINDOWS:
        raise ValueError(
            f"prediction_window must be one of {sorted(PREDICTION_WINDOWS)}."
        )
    model_role = model_role or ("primary" if is_public else "challenger")
    if model_role not in MODEL_ROLES:
        raise ValueError(f"model_role must be one of {sorted(MODEL_ROLES)}.")
    if probability_status not in PROBABILITY_STATUSES:
        raise ValueError(
            "probability_status must be one of "
            f"{sorted(PROBABILITY_STATUSES)}."
        )

    legacy_input = prediction_mode == "legacy_unknown"
    frozen = freeze_candidate_cohort(
        candidates, require_game_pk=not legacy_input
    )
    if candidate_cohort_id and candidate_cohort_id != frozen["candidate_cohort_id"]:
        raise ValueError(
            "Provided candidate_cohort_id does not match the candidates being stored."
        )
    candidate_cohort_id = candidate_cohort_id or frozen["candidate_cohort_id"]
    if candidate_manifest is None:
        candidate_manifest = frozen["candidate_manifest"]
    elif candidate_manifest != frozen["candidate_manifest"]:
        raise ValueError(
            "Provided candidate_manifest does not match the candidates being stored."
        )

    existing = await db.fetch_one(
        hit_pick_runs.select().where(hit_pick_runs.c.run_id == run_id)
    )
    if existing is not None:
        if (
            existing["pick_date"] != pick_date
            or existing["model_version"] != model_version
            or (
                existing["candidate_cohort_id"] != candidate_cohort_id
                and not (
                    legacy_input and existing["candidate_cohort_id"] is None
                )
            )
        ):
            raise ValueError(f"run_id {run_id} already exists with different inputs.")
        stored_count = int(existing["candidate_count"])
        return stored_count if top is None else min(stored_count, max(top, 0))

    rows = []
    candidates_to_store = candidates if top is None else candidates[:max(top, 0)]
    for rank, candidate in enumerate(candidates_to_store, start=1):
        row = {key: candidate.get(key) for key in _CANDIDATE_COLUMNS}
        row["game_pk"] = candidate.get("game_pk", candidate.get("game_id"))
        if row["game_pk"] is None and not legacy_input:
            raise ValueError(
                f"Candidate {candidate.get('player_id')} is missing required game_pk."
            )
        if row.get("platoon_advantage") is not None:
            row["platoon_advantage"] = int(row["platoon_advantage"])
        rows.append({
            "run_id": run_id,
            "pick_date": pick_date,
            "model_version": model_version,
            "is_public": 1 if is_public else 0,
            "generated_at": generated_at,
            "trained_on_rows": trained_on_rows,
            "rank": rank,
            **row,
        })
    run_row = {
        "run_id": run_id,
        "pick_date": pick_date,
        "model_version": model_version,
        "generated_at": generated_at,
        "as_of_timestamp": as_of_timestamp,
        "prediction_mode": prediction_mode,
        "comparison_group_id": comparison_group_id,
        "prediction_window": prediction_window,
        "model_role": model_role,
        "is_visible": 1 if is_visible else 0,
        "probability_status": probability_status,
        # Retained as a compatibility pointer for the original endpoint.
        # It no longer controls whether a challenger is reader-visible.
        "is_public": 1 if is_public else 0,
        "is_evaluation": 1 if is_evaluation else 0,
        "trained_on_rows": trained_on_rows,
        "candidate_cohort_id": candidate_cohort_id,
        "candidate_count": frozen["candidate_count"],
        "candidate_manifest_json": json.dumps(
            candidate_manifest, sort_keys=True, separators=(",", ":")
        ),
        "runtime_manifest_json": (
            json.dumps(runtime_manifest, sort_keys=True, separators=(",", ":"))
            if runtime_manifest is not None
            else None
        ),
        "created_at": _utc_now(),
    }
    async with db.transaction():
        date_match = hit_picks.c.pick_date == pick_date
        if model_role == "primary" and is_evaluation:
            await db.execute(
                hit_picks.update()
                .where(date_match & (hit_picks.c.is_public == 1))
                .values(is_public=0)
            )
            await db.execute(
                hit_pick_runs.update()
                .where(
                    (hit_pick_runs.c.pick_date == pick_date)
                    & (hit_pick_runs.c.is_public == 1)
                )
                .values(is_public=0)
            )
        if is_evaluation:
            await db.execute(
                hit_pick_runs.update()
                .where(
                    (hit_pick_runs.c.pick_date == pick_date)
                    & (hit_pick_runs.c.model_version == model_version)
                    & (hit_pick_runs.c.prediction_window == prediction_window)
                    & (hit_pick_runs.c.is_evaluation == 1)
                )
                .values(is_evaluation=0)
            )
        if model_role == "primary" and is_evaluation:
            # Only one primary evaluation board may lead a date/window. A
            # rerun remains immutable and visible by UUID after its evaluation
            # pointer is moved to the replacement.
            await db.execute(
                hit_pick_runs.update()
                .where(
                    (hit_pick_runs.c.pick_date == pick_date)
                    & (hit_pick_runs.c.prediction_window == prediction_window)
                    & (hit_pick_runs.c.model_role == "primary")
                    & (hit_pick_runs.c.is_evaluation == 1)
                )
                .values(is_evaluation=0, is_public=0)
            )
        await db.execute(hit_pick_runs.insert().values(**run_row))
        if rows:
            await db.execute_many(hit_picks.insert(), rows)
    return len(rows)


async def publish_paired_runs(
    *,
    primary_run: Mapping[str, Any],
    challenger_runs: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Publish the primary first, then isolate every challenger failure.

    V3 is allowed to fail loudly, but it is never allowed to roll back or
    prevent a valid V2 board. All runs must carry the same frozen-slate keys
    before they can be paired by the reader API.
    """
    primary = dict(primary_run)
    primary.setdefault("model_role", "primary")
    primary.setdefault("is_visible", True)
    primary.setdefault("is_public", True)
    primary_count = await replace_picks(**primary)
    result = {
        "primary": {
            "model_version": primary["model_version"],
            "stored": primary_count,
        },
        "challengers": [],
    }
    contract_keys = (
        "pick_date",
        "comparison_group_id",
        "candidate_cohort_id",
        "as_of_timestamp",
        "prediction_window",
    )
    for candidate_run in challenger_runs:
        challenger = dict(candidate_run)
        challenger.setdefault("model_role", "challenger")
        challenger.setdefault("is_visible", True)
        challenger.setdefault("is_public", False)
        try:
            mismatches = [
                key
                for key in contract_keys
                if challenger.get(key) != primary.get(key)
            ]
            if mismatches:
                raise ValueError(
                    "Challenger does not share the primary frozen-slate "
                    f"contract: {', '.join(mismatches)}."
                )
            stored = await replace_picks(**challenger)
            result["challengers"].append(
                {
                    "model_version": challenger["model_version"],
                    "stored": stored,
                    "status": "stored",
                }
            )
        except Exception as exc:  # noqa: BLE001 - isolation boundary is intentional
            result["challengers"].append(
                {
                    "model_version": challenger.get("model_version", "unknown"),
                    "stored": 0,
                    "status": "failed",
                    "error": str(exc),
                }
            )
    return result


async def apply_grades(
    *,
    pick_date: str,
    outcomes: Mapping[Any, Mapping[str, int]],
) -> int:
    """Fill the grading columns for one date's stored picks.

    New outcomes map (game_pk, player_id) -> statline. Legacy player-only
    outcomes remain accepted for old tests/backfills. A pick is not touched
    until its own game is final, which matters for partial slates.
    """
    db = await get_picks_db()
    rows = await db.fetch_all(
        hit_picks.select().where(hit_picks.c.pick_date == pick_date)
    )
    graded_at = _utc_now()
    final_game_pks = set(getattr(outcomes, "final_game_pks", set())) or {
        int(key[0])
        for key in outcomes
        if isinstance(key, tuple) and len(key) == 2
    }
    player_game_pks = getattr(outcomes, "player_game_pks", {})
    updated = 0
    async with db.transaction():
        for row in rows:
            player_id = int(row["player_id"])
            game_pk = _row_value(row, "game_pk")
            outcome = None
            if game_pk is not None:
                game_pk = int(game_pk)
                if final_game_pks and game_pk not in final_game_pks:
                    continue
                outcome = outcomes.get((game_pk, player_id))
            if outcome is None and player_id in outcomes:
                outcome = outcomes.get(player_id)
            if outcome is None and game_pk is None:
                if len(player_game_pks.get(player_id, set())) > 1:
                    continue
                matches = [
                    value
                    for key, value in outcomes.items()
                    if isinstance(key, tuple)
                    and len(key) == 2
                    and int(key[1]) == player_id
                ]
                # Never guess which game a legacy row meant in a doubleheader.
                if len(matches) == 1:
                    outcome = matches[0]
                elif len(matches) > 1:
                    continue
            values = {
                "played": 1 if outcome else 0,
                "hits": outcome["hits"] if outcome else None,
                "got_hit": (1 if outcome["hits"] >= 1 else 0) if outcome else None,
                "graded_at": graded_at,
            }
            for column in _STATLINE_COLUMNS:
                if column != "hits":
                    values[column] = outcome.get(column) if outcome else None
            await db.execute(
                hit_picks.update().where(hit_picks.c.id == row["id"]).values(**values)
            )
            updated += 1
    return updated


# ---------------------------------------------------------------------------
# Reads (called by the /hit-picks API routes)
# ---------------------------------------------------------------------------

def _row_value(row: Mapping[str, Any], key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def shape_pick_rows(
    rows: list[Mapping[str, Any]],
    *,
    available_models: Optional[list[dict[str, Any]]] = None,
) -> Optional[dict[str, Any]]:
    """Shape one model/date result for both latest and historical routes."""
    if not rows:
        return None
    first = rows[0]
    return {
        "run_id": _row_value(first, "run_id"),
        "date": first["pick_date"],
        "generated_at": first["generated_at"],
        "as_of_timestamp": _row_value(first, "as_of_timestamp"),
        "prediction_mode": _row_value(first, "prediction_mode"),
        "comparison_group_id": _row_value(first, "comparison_group_id"),
        "prediction_window": _row_value(first, "prediction_window"),
        "model_role": _row_value(first, "model_role"),
        "is_visible": bool(_row_value(first, "is_visible")),
        "probability_status": _row_value(first, "probability_status"),
        "candidate_cohort_id": _row_value(first, "candidate_cohort_id"),
        "candidate_count": _row_value(first, "candidate_count"),
        "model_version": first["model_version"],
        "trained_on_rows": first["trained_on_rows"],
        "grading_status": (
            "graded"
            if all(_row_value(row, "played") is not None for row in rows)
            else "pending"
        ),
        "available_models": available_models or [
            {
                "model_version": first["model_version"],
                "is_public": bool(_row_value(first, "is_public")),
                "model_role": _row_value(first, "model_role"),
                "is_visible": bool(_row_value(first, "is_visible")),
                "probability_status": _row_value(first, "probability_status"),
                "run_id": _row_value(first, "run_id"),
            }
        ],
        "picks": [
            {
                **{key: row[key] for key in _CANDIDATE_COLUMNS},
                "rank": row["rank"],
                "played": _row_value(row, "played"),
                "got_hit": _row_value(row, "got_hit"),
                **{key: _row_value(row, key) for key in _STATLINE_COLUMNS},
            }
            for row in rows
        ],
    }


def summarize_available_dates(
    rows: Iterable[Mapping[str, Any]],
    *,
    limit: int = 180,
) -> dict[str, Any]:
    """Turn public pick rows into calendar metadata, newest date first."""
    by_date: dict[str, dict[str, Any]] = {}
    for row in rows:
        pick_date = row["pick_date"]
        day = by_date.setdefault(
            pick_date,
            {
                "date": pick_date,
                "model_version": row["model_version"],
                "generated_at": _row_value(row, "generated_at"),
                "pick_count": 0,
                "played": 0,
                "hits": 0,
                "_all_graded": True,
            },
        )
        day["pick_count"] += 1
        played = _row_value(row, "played")
        if played is None:
            day["_all_graded"] = False
        elif played:
            day["played"] += 1
            day["hits"] += int(_row_value(row, "got_hit") or 0)

    dates = []
    for pick_date in sorted(by_date, reverse=True)[:max(limit, 0)]:
        day = by_date[pick_date]
        day["grading_status"] = "graded" if day.pop("_all_graded") else "pending"
        dates.append(day)
    return {
        "dates": dates,
        "latest_date": dates[0]["date"] if dates else None,
        "count": len(dates),
    }


async def _available_models_for_date(
    db: Database,
    pick_date: str,
) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        hit_pick_runs.select()
        .where(hit_pick_runs.c.pick_date == pick_date)
        .order_by(
            hit_pick_runs.c.is_visible.desc(),
            hit_pick_runs.c.is_evaluation.desc(),
            hit_pick_runs.c.generated_at.desc(),
        )
    )
    models = []
    seen = set()
    for row in rows:
        version = row["model_version"]
        if version in seen:
            continue
        seen.add(version)
        models.append(
            {
                "model_version": version,
                "is_public": bool(row["is_public"]),
                "model_role": row["model_role"],
                "is_visible": bool(row["is_visible"]),
                "probability_status": row["probability_status"],
                "generated_at": row["generated_at"],
                "run_id": row["run_id"],
                "as_of_timestamp": row["as_of_timestamp"],
                "prediction_mode": row["prediction_mode"],
                "candidate_cohort_id": row["candidate_cohort_id"],
                "comparison_group_id": row["comparison_group_id"],
                "prediction_window": row["prediction_window"],
            }
        )
    return models


async def fetch_picks_for_date(
    *,
    pick_date: str,
    top: int = 15,
    model_version: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Fetch the public run or one explicit immutable model/run snapshot."""
    db = await get_picks_db()
    run_query = hit_pick_runs.select().where(hit_pick_runs.c.pick_date == pick_date)
    if run_id:
        run_query = run_query.where(hit_pick_runs.c.run_id == run_id)
    elif model_version:
        run_query = (
            run_query.where(hit_pick_runs.c.model_version == model_version)
            .order_by(
                hit_pick_runs.c.is_evaluation.desc(),
                hit_pick_runs.c.generated_at.desc(),
            )
        )
    else:
        run_query = (
            run_query.where(hit_pick_runs.c.is_public == 1)
            .order_by(hit_pick_runs.c.generated_at.desc())
        )
    selected_run = await db.fetch_one(run_query.limit(1))
    if selected_run is None:
        return None

    rows = await db.fetch_all(
        hit_picks.select()
        .where(hit_picks.c.run_id == selected_run["run_id"])
        .order_by(hit_picks.c.rank)
        .limit(max(top, 0))
    )
    if not rows:
        return None
    run_metadata = dict(selected_run)
    shaped_rows = []
    for row in rows:
        shaped = dict(row)
        for key in (
            "as_of_timestamp",
            "prediction_mode",
            "candidate_cohort_id",
            "candidate_count",
            "is_evaluation",
            "comparison_group_id",
            "prediction_window",
            "model_role",
            "is_visible",
            "probability_status",
        ):
            shaped[key] = run_metadata[key]
        shaped_rows.append(shaped)
    models = await _available_models_for_date(db, pick_date)
    return shape_pick_rows(shaped_rows, available_models=models)


async def fetch_latest_picks(*, top: int = 15) -> Optional[dict[str, Any]]:
    """The most recent public pick list, shaped like the historical route."""
    db = await get_picks_db()
    latest = await db.fetch_one(
        "select max(pick_date) as pick_date from hit_pick_runs where is_public = 1"
    )
    if latest is None or latest["pick_date"] is None:
        return None
    return await fetch_picks_for_date(pick_date=latest["pick_date"], top=top)


async def fetch_available_dates(*, limit: int = 180) -> dict[str, Any]:
    """Calendar metadata for dates with a stored public pick list."""
    db = await get_picks_db()
    public_join = hit_picks.join(
        hit_pick_runs, hit_picks.c.run_id == hit_pick_runs.c.run_id
    )
    rows = await db.fetch_all(
        hit_picks.select()
        .select_from(public_join)
        .where(hit_pick_runs.c.is_public == 1)
        .order_by(hit_picks.c.pick_date.desc(), hit_picks.c.rank)
    )
    return summarize_available_dates([dict(row) for row in rows], limit=limit)


async def _selected_role_run(
    db: Database,
    *,
    model_role: str,
    pick_date: Optional[str] = None,
    prediction_window: Optional[str] = None,
) -> Optional[Mapping[str, Any]]:
    if model_role not in {"primary", "challenger"}:
        raise ValueError("model_role must be primary or challenger.")
    query = hit_pick_runs.select().where(
        (hit_pick_runs.c.model_role == model_role)
        & (hit_pick_runs.c.is_visible == 1)
        & (hit_pick_runs.c.is_evaluation == 1)
    )
    if pick_date:
        query = query.where(hit_pick_runs.c.pick_date == pick_date)
    if prediction_window:
        if prediction_window not in PREDICTION_WINDOWS - {"legacy"}:
            raise ValueError("prediction_window must be morning or afternoon.")
        query = query.where(hit_pick_runs.c.prediction_window == prediction_window)
    return await db.fetch_one(
        query.order_by(
            hit_pick_runs.c.pick_date.desc(),
            hit_pick_runs.c.generated_at.desc(),
        ).limit(1)
    )


async def _shape_run(
    db: Database,
    run: Mapping[str, Any],
    *,
    top: int,
) -> Optional[dict[str, Any]]:
    rows = await db.fetch_all(
        hit_picks.select()
        .where(hit_picks.c.run_id == run["run_id"])
        .order_by(hit_picks.c.rank)
        .limit(max(top, 0))
    )
    if not rows:
        return None
    shaped_rows = []
    run_metadata = dict(run)
    for row in rows:
        shaped = dict(row)
        shaped.update(
            {
                key: run_metadata.get(key)
                for key in (
                    "as_of_timestamp",
                    "prediction_mode",
                    "comparison_group_id",
                    "prediction_window",
                    "model_role",
                    "is_visible",
                    "probability_status",
                    "candidate_cohort_id",
                    "candidate_count",
                    "is_evaluation",
                )
            }
        )
        shaped_rows.append(shaped)
    return shape_pick_rows(shaped_rows)


async def fetch_role_picks(
    *,
    model_role: str,
    top: int = 15,
    pick_date: Optional[str] = None,
    prediction_window: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Return the active visible primary or challenger board."""
    db = await get_picks_db()
    run = await _selected_role_run(
        db,
        model_role=model_role,
        pick_date=pick_date,
        prediction_window=prediction_window,
    )
    if run is None:
        return None
    return await _shape_run(db, run, top=top)


async def fetch_role_dates(
    *,
    model_role: str,
    limit: int = 180,
) -> dict[str, Any]:
    """Calendar metadata for active evaluation boards of one role."""
    if model_role not in {"primary", "challenger"}:
        raise ValueError("model_role must be primary or challenger.")
    db = await get_picks_db()
    joined = hit_picks.join(
        hit_pick_runs, hit_picks.c.run_id == hit_pick_runs.c.run_id
    )
    rows = await db.fetch_all(
        hit_picks.select()
        .select_from(joined)
        .where(
            (hit_pick_runs.c.model_role == model_role)
            & (hit_pick_runs.c.is_visible == 1)
            & (hit_pick_runs.c.is_evaluation == 1)
        )
        .order_by(
            hit_picks.c.pick_date.desc(),
            hit_picks.c.generated_at.desc(),
            hit_picks.c.rank,
        )
    )
    # A date may have both morning and afternoon evaluation runs. The calendar
    # is date-grained, so keep only the newest run's rows.
    newest_run_by_date: dict[str, str] = {}
    selected = []
    for row in rows:
        pick_date = row["pick_date"]
        run_id = row["run_id"]
        newest_run_by_date.setdefault(pick_date, run_id)
        if newest_run_by_date[pick_date] == run_id:
            selected.append(dict(row))
    return summarize_available_dates(selected, limit=limit)


def _pick_identity(row: Mapping[str, Any]) -> tuple[Optional[int], int]:
    game_pk = _row_value(row, "game_pk")
    return (
        int(game_pk) if game_pk is not None else None,
        int(row["player_id"]),
    )


def shape_paired_comparison(
    *,
    primary_run: Mapping[str, Any],
    challenger_run: Optional[Mapping[str, Any]],
    primary_rows: Iterable[Mapping[str, Any]] = (),
    challenger_rows: Iterable[Mapping[str, Any]] = (),
    top: int = 15,
) -> dict[str, Any]:
    """Build a comparison only when both runs prove identical inputs."""
    base = {
        "date": primary_run["pick_date"],
        "prediction_window": primary_run["prediction_window"],
        "comparison_group_id": primary_run["comparison_group_id"],
        "candidate_cohort_id": primary_run["candidate_cohort_id"],
        "primary": {
            "run_id": primary_run["run_id"],
            "model_version": primary_run["model_version"],
            "probability_status": primary_run["probability_status"],
            "candidate_count": primary_run["candidate_count"],
        },
        "challenger": None,
        "rows": [],
    }
    if challenger_run is None:
        return {
            **base,
            "status": "no_paired_run",
            "comparable": False,
            "reason": "No visible V3 run was scored from this V2 snapshot.",
        }

    base["challenger"] = {
        "run_id": challenger_run["run_id"],
        "model_version": challenger_run["model_version"],
        "probability_status": challenger_run["probability_status"],
        "candidate_count": challenger_run["candidate_count"],
    }
    contract = (
        "pick_date",
        "comparison_group_id",
        "candidate_cohort_id",
        "as_of_timestamp",
        "prediction_window",
    )
    mismatches = [
        key for key in contract if primary_run[key] != challenger_run[key]
    ]
    if mismatches:
        return {
            **base,
            "status": "not_comparable",
            "comparable": False,
            "reason": (
                "Runs do not share the same frozen snapshot: "
                + ", ".join(mismatches)
            ),
        }

    primary_by_id = {_pick_identity(row): dict(row) for row in primary_rows}
    challenger_by_id = {_pick_identity(row): dict(row) for row in challenger_rows}
    identities = {
        identity
        for identity in primary_by_id.keys() | challenger_by_id.keys()
        if (
            primary_by_id.get(identity, {}).get("rank", top + 1) <= top
            or challenger_by_id.get(identity, {}).get("rank", top + 1) <= top
        )
    }
    rows = []
    for identity in identities:
        primary = primary_by_id.get(identity)
        challenger = challenger_by_id.get(identity)
        context = primary or challenger or {}
        p_rank = primary.get("rank") if primary else None
        c_rank = challenger.get("rank") if challenger else None
        p_score = primary.get("hit_probability") if primary else None
        c_score = challenger.get("hit_probability") if challenger else None
        actual = primary or challenger or {}
        rows.append(
            {
                "game_pk": identity[0],
                "player_id": identity[1],
                "player_name": context.get("player_name"),
                "team": context.get("team"),
                "opponent": context.get("opponent"),
                "batting_order": context.get("batting_order"),
                "lineup_source": context.get("lineup_source"),
                "pitcher_name": context.get("pitcher_name"),
                "primary_rank": p_rank,
                "challenger_rank": c_rank,
                "rank_movement": (
                    p_rank - c_rank
                    if p_rank is not None and c_rank is not None
                    else None
                ),
                "primary_score": p_score,
                "challenger_score": c_score,
                "score_delta": (
                    c_score - p_score
                    if p_score is not None and c_score is not None
                    else None
                ),
                "entered_top": c_rank is not None and c_rank <= top
                and (p_rank is None or p_rank > top),
                "left_top": p_rank is not None and p_rank <= top
                and (c_rank is None or c_rank > top),
                "context_matches": (
                    primary is not None
                    and challenger is not None
                    and all(
                        primary.get(key) == challenger.get(key)
                        for key in (
                            "team",
                            "opponent",
                            "batting_order",
                            "pitcher_id",
                            "lineup_source",
                        )
                    )
                ),
                "played": actual.get("played"),
                "got_hit": actual.get("got_hit"),
                **{key: actual.get(key) for key in _STATLINE_COLUMNS},
            }
        )
    rows.sort(
        key=lambda row: (
            min(
                row["primary_rank"] or top + 1,
                row["challenger_rank"] or top + 1,
            ),
            row["player_name"] or "",
        )
    )
    shared = len(primary_by_id.keys() & challenger_by_id.keys())
    runtime = json.loads(challenger_run.get("runtime_manifest_json") or "{}")
    return {
        **base,
        "status": "paired",
        "comparable": True,
        "reason": None,
        "coverage": {
            "shared_candidates": shared,
            "primary_candidates": len(primary_by_id),
            "challenger_candidates": len(challenger_by_id),
            "challenger_fraction": (
                round(shared / len(primary_by_id), 4) if primary_by_id else None
            ),
        },
        "fallbacks": runtime.get("fallbacks", {}),
        "rows": rows,
    }


async def fetch_paired_comparison(
    *,
    pick_date: Optional[str] = None,
    prediction_window: Optional[str] = None,
    top: int = 15,
) -> Optional[dict[str, Any]]:
    db = await get_picks_db()
    primary = await _selected_role_run(
        db,
        model_role="primary",
        pick_date=pick_date,
        prediction_window=prediction_window,
    )
    if primary is None:
        return None
    challenger = None
    if primary["comparison_group_id"]:
        challenger = await db.fetch_one(
            hit_pick_runs.select()
            .where(
                (hit_pick_runs.c.model_role == "challenger")
                & (hit_pick_runs.c.is_visible == 1)
                & (hit_pick_runs.c.is_evaluation == 1)
                & (
                    hit_pick_runs.c.comparison_group_id
                    == primary["comparison_group_id"]
                )
            )
            .order_by(hit_pick_runs.c.generated_at.desc())
            .limit(1)
        )
    primary_rows = await db.fetch_all(
        hit_picks.select()
        .where(hit_picks.c.run_id == primary["run_id"])
        .order_by(hit_picks.c.rank)
    )
    challenger_rows = []
    if challenger is not None:
        challenger_rows = await db.fetch_all(
            hit_picks.select()
            .where(hit_picks.c.run_id == challenger["run_id"])
            .order_by(hit_picks.c.rank)
        )
    return shape_paired_comparison(
        primary_run=dict(primary),
        challenger_run=dict(challenger) if challenger else None,
        primary_rows=[dict(row) for row in primary_rows],
        challenger_rows=[dict(row) for row in challenger_rows],
        top=top,
    )


def summarize_pick_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate graded pick rows into per-model-version hit rates.

    Same output shape as grade_hit_picks.summarize_ledger. Pure function:
    pass any iterable of dicts with model_version / pick_date / rank /
    played / got_hit keys.
    """
    by_version: dict[str, dict[str, Any]] = {}
    dates_by_version: dict[str, set] = {}
    for row in rows:
        if row["played"] is None:
            continue  # not graded yet
        version = row["model_version"] or "unknown"
        agg = by_version.setdefault(
            version,
            {f"top{n}": {"played": 0, "hits": 0} for n in TOP_NS},
        )
        dates_by_version.setdefault(version, set()).add(row["pick_date"])
        for n in TOP_NS:
            if row["rank"] <= n and row["played"]:
                agg[f"top{n}"]["played"] += 1
                agg[f"top{n}"]["hits"] += int(row["got_hit"] or 0)

    for version, agg in by_version.items():
        agg["days"] = len(dates_by_version[version])
        for n in TOP_NS:
            bucket = agg[f"top{n}"]
            bucket["hit_rate"] = (
                round(bucket["hits"] / bucket["played"], 4) if bucket["played"] else None
            )
    return by_version


async def fetch_ledger_summary(
    *,
    prediction_window: Optional[str] = None,
) -> dict[str, Any]:
    """Track evaluation boards without mixing two snapshots from one day.

    With no explicit window, afternoon is preferred, then morning, then
    legacy. `by_window` preserves separate morning/afternoon evaluation.
    """
    if prediction_window and prediction_window not in PREDICTION_WINDOWS:
        raise ValueError(
            f"prediction_window must be one of {sorted(PREDICTION_WINDOWS)}."
        )
    db = await get_picks_db()
    evaluation_join = hit_picks.join(
        hit_pick_runs, hit_picks.c.run_id == hit_pick_runs.c.run_id
    )
    rows = await db.fetch_all(
        hit_picks.select()
        .with_only_columns(
            hit_picks.c.model_version,
            hit_picks.c.pick_date,
            hit_picks.c.rank,
            hit_picks.c.played,
            hit_picks.c.got_hit,
            hit_picks.c.run_id,
            hit_pick_runs.c.prediction_window,
            hit_pick_runs.c.generated_at,
        )
        .select_from(evaluation_join)
        .where(
            (hit_picks.c.played.is_not(None))
            & (hit_pick_runs.c.is_evaluation == 1)
        )
    )
    records = [dict(row) for row in rows]
    by_window = {
        window: summarize_pick_rows(
            row for row in records if row["prediction_window"] == window
        )
        for window in sorted(PREDICTION_WINDOWS)
        if any(row["prediction_window"] == window for row in records)
    }
    if prediction_window:
        selected = [
            row for row in records if row["prediction_window"] == prediction_window
        ]
    else:
        precedence = {"legacy": 0, "morning": 1, "afternoon": 2}
        chosen: dict[tuple[str, str], tuple[int, str]] = {}
        for row in records:
            key = (row["model_version"] or "unknown", row["pick_date"])
            candidate = (
                precedence.get(row["prediction_window"], -1),
                row["generated_at"] or "",
            )
            if candidate > chosen.get(key, (-1, "")):
                chosen[key] = candidate
        selected = [
            row
            for row in records
            if (
                precedence.get(row["prediction_window"], -1),
                row["generated_at"] or "",
            )
            == chosen[(row["model_version"] or "unknown", row["pick_date"])]
        ]
    summary = summarize_pick_rows(selected)
    days_graded = len({row["pick_date"] for row in selected})
    return {
        "summary": summary,
        "by_window": by_window,
        "prediction_window": prediction_window or "preferred",
        "days_graded": days_graded,
    }


# ---------------------------------------------------------------------------
# Backfill CLI — load existing local JSON pick files into the database
# ---------------------------------------------------------------------------

async def _backfill(picks_dir: Path) -> None:
    from urllib.parse import urlparse

    host = urlparse(picks_database_url().replace("+asyncpg", "")).hostname
    print(f"Backfilling into picks database at: {host}")
    try:
        for pick_file in sorted(picks_dir.glob("hit_picks_*.json")):
            match = _PICK_FILE_RE.search(pick_file.name)
            if not match:
                continue
            payload = json.loads(pick_file.read_text(encoding="utf-8"))
            pick_date = match.group(1)
            model_version = payload.get("model_version") or "hit_logistic_v1"
            run_id = payload.get("run_id") or str(
                uuid5(
                    NAMESPACE_URL,
                    f"mlb-fantasy-stats/hit-picks/{pick_date}/{model_version}",
                )
            )
            count = await replace_picks(
                pick_date=pick_date,
                model_version=model_version,
                generated_at=payload.get("generated_at"),
                trained_on_rows=payload.get("trained_on_rows"),
                candidates=payload.get("candidates", []),
                run_id=run_id,
                as_of_timestamp=payload.get("as_of_timestamp"),
                prediction_mode=payload.get("prediction_mode") or "legacy_unknown",
                candidate_cohort_id=payload.get("candidate_cohort_id"),
                candidate_manifest=payload.get("candidate_manifest"),
                runtime_manifest=payload.get("runtime"),
            )
            print(f"{pick_date}: stored {count} picks ({model_version})")
        print("Backfill complete. Run grade_hit_picks.py --regrade to grade them into the DB.")
    finally:
        await close_picks_db()


def main() -> int:
    parser = argparse.ArgumentParser(description="Hit picks DB utilities.")
    parser.add_argument("--backfill", action="store_true", help="Load local hit_picks_*.json files into the database.")
    parser.add_argument("--picks-dir", default=str(BACKEND_DIR / "backtest_results"), help="Directory containing hit_picks_*.json.")
    args = parser.parse_args()
    if not args.backfill:
        parser.error("Nothing to do. Pass --backfill.")
    asyncio.run(_backfill(Path(args.picks_dir)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
