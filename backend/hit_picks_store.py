"""
hit_picks_store.py - Database persistence for daily hit-model picks.

Why this exists: predict_hits_today.py and grade_hit_picks.py run on the
machine that has the boxscore cache and training data, but the deployed
backend (Render) needs to SERVE the picks. This module is the bridge:
the scripts write picks and grades into the shared Postgres database,
and the /hit-picks API routes read them back out — same pattern as the
daily stats update.

Layout: one row per (pick_date, rank) in the `hit_picks` table. Grading
columns start NULL and get filled the morning after, once boxscores are
final. The summary math is a pure function so it can be unit tested
without a database.

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

from databases import Database
from dotenv import load_dotenv

from database import normalize_database_url
from models import hit_picks

BACKEND_DIR = Path(__file__).resolve().parent

# How many ranked picks to persist per day. The UI shows 15; grading uses
# top-5/10/15; a little headroom costs nothing.
STORED_PICKS_PER_DAY = 25

TOP_NS = (5, 10, 15)

# Candidate-dict keys copied straight into same-named table columns.
_CANDIDATE_COLUMNS = [
    "player_id", "player_name", "team", "opponent", "venue",
    "batting_order", "bats", "pitcher_id", "pitcher_name", "pitcher_throws",
    "lineup_source", "hit_probability", "season_hit_per_pa",
    "last10_hit_per_pa", "platoon_advantage",
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
) -> int:
    """Replace the stored pick list for one date (idempotent re-runs)."""
    db = await get_picks_db()
    rows = []
    for rank, candidate in enumerate(candidates[:top], start=1):
        row = {key: candidate.get(key) for key in _CANDIDATE_COLUMNS}
        if row.get("platoon_advantage") is not None:
            row["platoon_advantage"] = int(row["platoon_advantage"])
        rows.append({
            "pick_date": pick_date,
            "model_version": model_version,
            "generated_at": generated_at,
            "trained_on_rows": trained_on_rows,
            "rank": rank,
            **row,
        })
    async with db.transaction():
        await db.execute(hit_picks.delete().where(hit_picks.c.pick_date == pick_date))
        if rows:
            await db.execute_many(hit_picks.insert(), rows)
    return len(rows)


async def apply_grades(
    *,
    pick_date: str,
    outcomes: Mapping[int, Mapping[str, int]],
) -> int:
    """Fill the grading columns for one date's stored picks.

    `outcomes` maps player_id -> {hits, pa} for everyone who batted that
    day (from grade_hit_picks.outcomes_for_date). Stored picks missing
    from it are marked played=0 (scratched / lineup projection missed).
    """
    db = await get_picks_db()
    rows = await db.fetch_all(
        hit_picks.select().where(hit_picks.c.pick_date == pick_date)
    )
    graded_at = _utc_now()
    updated = 0
    async with db.transaction():
        for row in rows:
            outcome = outcomes.get(int(row["player_id"]))
            values = {
                "played": 1 if outcome else 0,
                "hits": outcome["hits"] if outcome else None,
                "got_hit": (1 if outcome["hits"] >= 1 else 0) if outcome else None,
                "graded_at": graded_at,
            }
            await db.execute(
                hit_picks.update().where(hit_picks.c.id == row["id"]).values(**values)
            )
            updated += 1
    return updated


# ---------------------------------------------------------------------------
# Reads (called by the /hit-picks API routes)
# ---------------------------------------------------------------------------

async def fetch_latest_picks(*, top: int = 15) -> Optional[dict[str, Any]]:
    """The most recent day's pick list, shaped like the JSON pick files."""
    db = await get_picks_db()
    latest = await db.fetch_one(
        "select max(pick_date) as pick_date from hit_picks"
    )
    if latest is None or latest["pick_date"] is None:
        return None
    rows = await db.fetch_all(
        hit_picks.select()
        .where(hit_picks.c.pick_date == latest["pick_date"])
        .order_by(hit_picks.c.rank)
        .limit(max(top, 0))
    )
    if not rows:
        return None
    first = rows[0]
    return {
        "date": first["pick_date"],
        "generated_at": first["generated_at"],
        "model_version": first["model_version"],
        "trained_on_rows": first["trained_on_rows"],
        "picks": [
            {
                **{key: row[key] for key in _CANDIDATE_COLUMNS},
                "rank": row["rank"],
                "played": row["played"],
                "got_hit": row["got_hit"],
            }
            for row in rows
        ],
    }


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
    rows = await db.fetch_all(
        "select model_version, pick_date, rank, played, got_hit "
        "from hit_picks where played is not null"
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
            count = await replace_picks(
                pick_date=match.group(1),
                model_version=payload.get("model_version") or "hit_logistic_v1",
                generated_at=payload.get("generated_at"),
                trained_on_rows=payload.get("trained_on_rows"),
                candidates=payload.get("candidates", []),
            )
            print(f"{match.group(1)}: stored {count} picks "
                  f"({payload.get('model_version') or 'hit_logistic_v1'})")
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
