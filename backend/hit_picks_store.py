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
date/model feeds the live ledger, so operational reruns cannot double-count.
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

# How many ranked picks to persist per day. The UI shows 15; grading uses
# top-5/10/15; a little headroom costs nothing.
STORED_PICKS_PER_DAY = 25

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
    top: int = STORED_PICKS_PER_DAY,
    is_public: bool = True,
    is_evaluation: bool = True,
    run_id: Optional[str] = None,
    as_of_timestamp: Optional[str] = None,
    prediction_mode: str = "legacy_unknown",
    candidate_cohort_id: Optional[str] = None,
    candidate_manifest: Optional[Mapping[str, Any]] = None,
    runtime_manifest: Optional[Mapping[str, Any]] = None,
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
        return min(int(existing["candidate_count"]), max(top, 0))

    rows = []
    for rank, candidate in enumerate(candidates[:top], start=1):
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
        if is_public:
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
                    & (hit_pick_runs.c.is_evaluation == 1)
                )
                .values(is_evaluation=0)
            )
        await db.execute(hit_pick_runs.insert().values(**run_row))
        if rows:
            await db.execute_many(hit_picks.insert(), rows)
    return len(rows)


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
            hit_pick_runs.c.is_public.desc(),
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
                "generated_at": row["generated_at"],
                "run_id": row["run_id"],
                "as_of_timestamp": row["as_of_timestamp"],
                "prediction_mode": row["prediction_mode"],
                "candidate_cohort_id": row["candidate_cohort_id"],
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


async def fetch_ledger_summary() -> dict[str, Any]:
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
        )
        .select_from(evaluation_join)
        .where(
            (hit_picks.c.played.is_not(None))
            & (hit_pick_runs.c.is_evaluation == 1)
        )
    )
    summary = summarize_pick_rows([dict(row) for row in rows])
    days_graded = len({row["pick_date"] for row in rows})
    return {"summary": summary, "days_graded": days_graded}


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
