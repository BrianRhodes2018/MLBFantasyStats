"""Persistence, grading, and read models for Pitcher Ks projection runs."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median
from typing import Any, Iterable, Mapping, Optional
from uuid import UUID

from databases import Database
from sqlalchemy import and_, desc, select

from database import database
from models import pitcher_k_predictions, pitcher_k_runs
from .modeling import APPROACH_ORDER


APPROACHES = set(APPROACH_ORDER)
RESULT_STATUSES = {
    "pending",
    "graded",
    "did_not_start",
    "postponed",
    "suspended",
    "cancelled",
    "data_unavailable",
}
TERMINAL_RESULT_STATUSES = {"graded", "did_not_start", "cancelled"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_uuid(value: str, field: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a UUID string.") from exc


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _value(record: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        return record[key]
    except (KeyError, TypeError):
        return default


def _result_status(record: Mapping[str, Any]) -> str:
    status = _value(record, "result_status")
    if status in RESULT_STATUSES:
        return str(status)
    return "graded" if _value(record, "actual_ks") is not None else "pending"


def _run_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "run_id": record["run_id"],
        "projection_date": record["projection_date"],
        "approach": record["approach"],
        "model_version": record["model_version"],
        "generated_at": record["generated_at"],
        "as_of_timestamp": record["as_of_timestamp"],
        "prediction_window": record["prediction_window"],
        "comparison_group_id": record["comparison_group_id"],
        "candidate_cohort_id": record["candidate_cohort_id"],
        "trained_through": record["trained_through"],
        "trained_on_rows": record["trained_on_rows"],
        "candidate_count": record["candidate_count"],
        "backtest": json.loads(record["backtest_metrics_json"]),
        "model_manifest": json.loads(record["model_manifest_json"]),
    }


def _prediction_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    payload = {
        "rank": record["rank"],
        "game_pk": record["game_pk"],
        "game_time": record["game_time"],
        "pitcher_id": record["pitcher_id"],
        "pitcher_name": record["pitcher_name"],
        "team": record["team"],
        "opponent": record["opponent"],
        "venue": record["venue"],
        "pitcher_throws": record["pitcher_throws"],
        "lineup_source": record["lineup_source"],
        "lineup_confidence": record["lineup_confidence"],
        "projected_ks": record["projected_ks"],
        "median_ks": record["median_ks"],
        "p10_ks": record["p10_ks"],
        "p90_ks": record["p90_ks"],
        "probability_5_plus": record["probability_5_plus"],
        "probability_6_plus": record["probability_6_plus"],
        "projected_batters_faced": record["projected_batters_faced"],
        "pmf": json.loads(record["pmf_json"]),
        "actual_ks": _value(record, "actual_ks"),
        "actual_batters_faced": _value(record, "actual_batters_faced"),
        "actual_innings_pitched": _value(record, "actual_innings_pitched"),
        "actual_pitch_count": _value(record, "actual_pitch_count"),
        "result_status": _result_status(record),
        "started": _value(record, "started"),
        "game_status": _value(record, "game_status"),
        "grading_source": _value(record, "grading_source"),
        "grade_detail": _value(record, "grade_detail"),
        "graded_at": _value(record, "graded_at"),
    }
    if payload["actual_ks"] is not None:
        error = float(payload["projected_ks"]) - int(payload["actual_ks"])
        payload["error"] = round(error, 2)
        payload["absolute_error"] = round(abs(error), 2)
    else:
        payload["error"] = None
        payload["absolute_error"] = None
    return payload


def evaluation_metrics(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Calculate transparent live metrics for one set of published rows."""
    records = list(rows)
    statuses = Counter(_result_status(row) for row in records)
    graded = [
        row
        for row in records
        if _result_status(row) == "graded" and _value(row, "actual_ks") is not None
    ]
    errors = [
        float(row["projected_ks"]) - int(row["actual_ks"])
        for row in graded
    ]

    def rounded(value: Optional[float]) -> Optional[float]:
        return round(value, 4) if value is not None else None

    if errors:
        absolute_errors = [abs(error) for error in errors]
        mae = sum(absolute_errors) / len(absolute_errors)
        rmse = math.sqrt(sum(error * error for error in errors) / len(errors))
        bias = sum(errors) / len(errors)
        median_absolute_error = float(median(absolute_errors))
        within_one_rate = sum(error <= 1 for error in absolute_errors) / len(errors)
        interval_coverage = sum(
            int(row["p10_ks"]) <= int(row["actual_ks"]) <= int(row["p90_ks"])
            for row in graded
        ) / len(graded)
        brier_5 = sum(
            (float(row["probability_5_plus"]) - int(int(row["actual_ks"]) >= 5)) ** 2
            for row in graded
        ) / len(graded)
        brier_6 = sum(
            (float(row["probability_6_plus"]) - int(int(row["actual_ks"]) >= 6)) ** 2
            for row in graded
        ) / len(graded)
    else:
        mae = rmse = bias = median_absolute_error = None
        within_one_rate = interval_coverage = brier_5 = brier_6 = None

    unresolved = sum(
        count
        for status, count in statuses.items()
        if status not in TERMINAL_RESULT_STATUSES
    )
    return {
        "projections": len(records),
        "graded_starts": len(graded),
        "did_not_start": statuses["did_not_start"],
        "pending": unresolved,
        "complete": bool(records) and unresolved == 0,
        "status_counts": dict(sorted(statuses.items())),
        "mae": rounded(mae),
        "rmse": rounded(rmse),
        "bias": rounded(bias),
        "median_absolute_error": rounded(median_absolute_error),
        "within_one_rate": rounded(within_one_rate),
        "interval_80_coverage": rounded(interval_coverage),
        "brier_5_plus": rounded(brier_5),
        "brier_6_plus": rounded(brier_6),
    }


async def store_daily_bundle(
    bundle: Mapping[str, Any],
    *,
    db: Database,
) -> dict[str, int]:
    """Publish all three approach runs atomically for one frozen slate."""
    projection_date = str(bundle["projection_date"])
    comparison_group_id = _validate_uuid(
        str(bundle["comparison_group_id"]),
        "comparison_group_id",
    )
    generated_at = str(bundle["generated_at"])
    as_of_timestamp = str(bundle["as_of_timestamp"])
    prediction_window = str(bundle["prediction_window"])
    candidate_cohort_id = str(bundle["candidate_cohort_id"])
    runs = bundle.get("runs") or {}
    if set(runs) != APPROACHES:
        raise ValueError("A Pitcher Ks bundle must contain all three approaches.")

    counts: dict[str, int] = {}
    async with db.transaction():
        for approach in APPROACH_ORDER:
            run = runs[approach]
            run_id = _validate_uuid(str(run["run_id"]), "run_id")
            predictions = list(run.get("predictions") or [])
            if not predictions:
                raise ValueError(f"{approach} has no predictions to publish.")
            existing = await db.fetch_one(
                pitcher_k_runs.select().where(pitcher_k_runs.c.run_id == run_id)
            )
            if existing is not None:
                counts[approach] = int(existing["candidate_count"])
                continue

            await db.execute(
                pitcher_k_runs.update()
                .where(
                    and_(
                        pitcher_k_runs.c.projection_date == projection_date,
                        pitcher_k_runs.c.approach == approach,
                        pitcher_k_runs.c.prediction_window == prediction_window,
                        pitcher_k_runs.c.is_public == 1,
                    )
                )
                .values(is_public=0, is_evaluation=0)
            )
            await db.execute(
                pitcher_k_runs.insert().values(
                    run_id=run_id,
                    projection_date=projection_date,
                    approach=approach,
                    model_version=str(run["model_version"]),
                    generated_at=generated_at,
                    as_of_timestamp=as_of_timestamp,
                    prediction_window=prediction_window,
                    comparison_group_id=comparison_group_id,
                    candidate_cohort_id=candidate_cohort_id,
                    trained_through=str(run["trained_through"]),
                    trained_on_rows=int(run["trained_on_rows"]),
                    candidate_count=len(predictions),
                    backtest_metrics_json=_json(run.get("backtest") or {}),
                    model_manifest_json=_json(run.get("model_manifest") or {}),
                    is_public=1,
                    is_evaluation=1,
                    created_at=_utc_now(),
                )
            )
            rows: list[dict[str, Any]] = []
            ranked = sorted(
                predictions,
                key=lambda prediction: (
                    -float(prediction["projected_ks"]),
                    str(prediction["pitcher_name"]),
                ),
            )
            for rank, prediction in enumerate(ranked, start=1):
                rows.append({
                    "run_id": run_id,
                    "projection_date": projection_date,
                    "approach": approach,
                    "rank": rank,
                    "game_pk": int(prediction["game_pk"]),
                    "game_time": prediction.get("game_time"),
                    "pitcher_id": int(prediction["pitcher_id"]),
                    "pitcher_name": str(prediction["pitcher_name"]),
                    "team": str(prediction["team"]),
                    "opponent": str(prediction["opponent"]),
                    "venue": prediction.get("venue"),
                    "pitcher_throws": prediction.get("pitcher_throws"),
                    "lineup_source": str(prediction.get("lineup_source") or "unknown"),
                    "lineup_confidence": float(prediction.get("lineup_confidence") or 0.0),
                    "projected_ks": float(prediction["projected_ks"]),
                    "median_ks": int(prediction["median_ks"]),
                    "p10_ks": int(prediction["p10_ks"]),
                    "p90_ks": int(prediction["p90_ks"]),
                    "probability_5_plus": float(prediction["probability_5_plus"]),
                    "probability_6_plus": float(prediction["probability_6_plus"]),
                    "projected_batters_faced": float(prediction["projected_batters_faced"]),
                    "pmf_json": _json(prediction["pmf"]),
                    "result_status": "pending",
                })
            await db.execute_many(pitcher_k_predictions.insert(), rows)
            counts[approach] = len(rows)
    return counts


async def _fetch_run_predictions(
    run_record: Mapping[str, Any],
    *,
    db: Database,
) -> dict[str, Any]:
    prediction_records = await db.fetch_all(
        select(pitcher_k_predictions)
        .where(pitcher_k_predictions.c.run_id == run_record["run_id"])
        .order_by(pitcher_k_predictions.c.rank)
    )
    payload = _run_payload(run_record)
    predictions = [_prediction_payload(record) for record in prediction_records]
    payload["predictions"] = predictions
    payload["evaluation"] = evaluation_metrics(predictions)
    return payload


async def _select_approach_run(
    approach: str,
    *,
    projection_date: Optional[str],
    db: Database,
) -> Optional[Mapping[str, Any]]:
    if approach not in APPROACHES:
        raise ValueError(f"Unknown Pitcher Ks approach: {approach}")
    query = select(pitcher_k_runs).where(
        and_(
            pitcher_k_runs.c.approach == approach,
            pitcher_k_runs.c.is_public == 1,
        )
    )
    if projection_date:
        query = query.where(pitcher_k_runs.c.projection_date == projection_date)
    return await db.fetch_one(
        query.order_by(
            desc(pitcher_k_runs.c.projection_date),
            desc(pitcher_k_runs.c.generated_at),
        ).limit(1)
    )


async def fetch_approach(
    approach: str,
    *,
    projection_date: Optional[str] = None,
    db: Database = database,
) -> Optional[dict[str, Any]]:
    run = await _select_approach_run(
        approach,
        projection_date=projection_date,
        db=db,
    )
    if run is None:
        return None
    return await _fetch_run_predictions(run, db=db)


async def fetch_latest_approach(
    approach: str,
    *,
    db: Database = database,
) -> Optional[dict[str, Any]]:
    return await fetch_approach(approach, db=db)


async def fetch_approach_dates(
    approach: str,
    *,
    limit: int = 180,
    db: Database = database,
) -> dict[str, Any]:
    if approach not in APPROACHES:
        raise ValueError(f"Unknown Pitcher Ks approach: {approach}")
    run_records = await db.fetch_all(
        select(pitcher_k_runs)
        .where(
            and_(
                pitcher_k_runs.c.approach == approach,
                pitcher_k_runs.c.is_public == 1,
            )
        )
        .order_by(
            desc(pitcher_k_runs.c.projection_date),
            desc(pitcher_k_runs.c.generated_at),
        )
    )
    selected_by_date: dict[str, Mapping[str, Any]] = {}
    for run in run_records:
        selected_by_date.setdefault(str(run["projection_date"]), run)
    selected = list(selected_by_date.values())[:max(limit, 0)]
    if not selected:
        return {"dates": [], "latest_date": None, "count": 0}

    run_ids = [run["run_id"] for run in selected]
    prediction_records = await db.fetch_all(
        select(pitcher_k_predictions).where(
            pitcher_k_predictions.c.run_id.in_(run_ids)
        )
    )
    by_run: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for prediction in prediction_records:
        by_run[str(prediction["run_id"])].append(prediction)

    dates = []
    for run in selected:
        metrics = evaluation_metrics(by_run[str(run["run_id"])])
        dates.append({
            "date": run["projection_date"],
            "grading_status": "graded" if metrics["complete"] else "pending",
            "prediction_window": run["prediction_window"],
            "model_version": run["model_version"],
            "projection_count": metrics["projections"],
            "graded_count": metrics["graded_starts"],
            "did_not_start_count": metrics["did_not_start"],
            "pending_count": metrics["pending"],
        })
    return {
        "dates": dates,
        "latest_date": dates[0]["date"] if dates else None,
        "count": len(dates),
    }


async def _comparison_runs(
    *,
    projection_date: Optional[str],
    db: Database,
) -> Optional[dict[str, Mapping[str, Any]]]:
    query = select(pitcher_k_runs).where(pitcher_k_runs.c.is_public == 1)
    if projection_date:
        query = query.where(pitcher_k_runs.c.projection_date == projection_date)
    records = await db.fetch_all(
        query.order_by(
            desc(pitcher_k_runs.c.projection_date),
            desc(pitcher_k_runs.c.generated_at),
        )
    )
    by_group: dict[str, dict[str, Mapping[str, Any]]] = {}
    group_order: list[str] = []
    for record in records:
        group_id = str(record["comparison_group_id"])
        if group_id not in by_group:
            by_group[group_id] = {}
            group_order.append(group_id)
        by_group[group_id].setdefault(str(record["approach"]), record)
    for group_id in group_order:
        if set(by_group[group_id]) == APPROACHES:
            return by_group[group_id]
    return None


async def fetch_comparison(
    *,
    projection_date: Optional[str] = None,
    db: Database = database,
) -> Optional[dict[str, Any]]:
    by_approach = await _comparison_runs(projection_date=projection_date, db=db)
    if by_approach is None:
        return None
    latest = max(by_approach.values(), key=lambda row: str(row["generated_at"]))
    approach_payloads = {
        approach: await _fetch_run_predictions(by_approach[approach], db=db)
        for approach in APPROACH_ORDER
    }
    rows_by_identity: dict[tuple[int, int], dict[str, Any]] = {}
    result_keys = (
        "actual_ks",
        "actual_batters_faced",
        "actual_innings_pitched",
        "actual_pitch_count",
        "result_status",
        "started",
        "game_status",
        "grading_source",
        "grade_detail",
        "graded_at",
    )
    for approach, payload in approach_payloads.items():
        for prediction in payload["predictions"]:
            identity = (prediction["game_pk"], prediction["pitcher_id"])
            row = rows_by_identity.setdefault(identity, {
                key: prediction[key]
                for key in (
                    "game_pk",
                    "game_time",
                    "pitcher_id",
                    "pitcher_name",
                    "team",
                    "opponent",
                    "venue",
                    "lineup_source",
                    *result_keys,
                )
            })
            row[approach] = {
                key: prediction[key]
                for key in (
                    "projected_ks",
                    "median_ks",
                    "p10_ks",
                    "p90_ks",
                    "probability_5_plus",
                    "probability_6_plus",
                    "projected_batters_faced",
                    "error",
                    "absolute_error",
                )
            }

    rows = []
    for row in rows_by_identity.values():
        if not all(approach in row for approach in APPROACH_ORDER):
            continue
        estimates = [float(row[approach]["projected_ks"]) for approach in APPROACH_ORDER]
        row["model_spread"] = round(max(estimates) - min(estimates), 2)
        if row["actual_ks"] is not None and row["result_status"] == "graded":
            smallest = min(float(row[approach]["absolute_error"]) for approach in APPROACH_ORDER)
            row["closest_approaches"] = [
                approach
                for approach in APPROACH_ORDER
                if float(row[approach]["absolute_error"]) == smallest
            ]
        else:
            row["closest_approaches"] = []
        rows.append(row)
    rows.sort(
        key=lambda row: (
            -max(row[approach]["projected_ks"] for approach in APPROACH_ORDER),
            row["pitcher_name"],
        )
    )

    approach_evaluation = {}
    for approach in APPROACH_ORDER:
        flattened = [
            {
                **row[approach],
                "actual_ks": row["actual_ks"],
                "result_status": row["result_status"],
            }
            for row in rows
        ]
        approach_evaluation[approach] = evaluation_metrics(flattened)
    graded_counts = [metric["graded_starts"] for metric in approach_evaluation.values()]
    best_approaches: list[str] = []
    if graded_counts and min(graded_counts) > 0:
        best_mae = min(
            float(metric["mae"])
            for metric in approach_evaluation.values()
            if metric["mae"] is not None
        )
        best_approaches = [
            approach
            for approach, metric in approach_evaluation.items()
            if metric["mae"] == best_mae
        ]

    return {
        "projection_date": latest["projection_date"],
        "generated_at": latest["generated_at"],
        "as_of_timestamp": latest["as_of_timestamp"],
        "prediction_window": latest["prediction_window"],
        "comparison_group_id": latest["comparison_group_id"],
        "candidate_cohort_id": latest["candidate_cohort_id"],
        "approaches": {
            approach: {
                key: approach_payloads[approach][key]
                for key in (
                    "run_id",
                    "model_version",
                    "trained_through",
                    "trained_on_rows",
                    "backtest",
                )
            }
            for approach in APPROACH_ORDER
        },
        "evaluation": {
            "complete": all(metric["complete"] for metric in approach_evaluation.values()),
            "graded_starters": min(graded_counts) if graded_counts else 0,
            "best_approaches": best_approaches,
            "approaches": approach_evaluation,
        },
        "rows": rows,
    }


async def fetch_latest_comparison(*, db: Database = database) -> Optional[dict[str, Any]]:
    return await fetch_comparison(db=db)


async def fetch_prediction_identities(
    projection_date: str,
    *,
    db: Database = database,
) -> list[dict[str, Any]]:
    records = await db.fetch_all(
        select(
            pitcher_k_predictions.c.game_pk,
            pitcher_k_predictions.c.pitcher_id,
            pitcher_k_predictions.c.pitcher_name,
        )
        .where(pitcher_k_predictions.c.projection_date == projection_date)
        .order_by(pitcher_k_predictions.c.game_pk, pitcher_k_predictions.c.pitcher_id)
    )
    unique: dict[tuple[int, int], dict[str, Any]] = {}
    for record in records:
        identity = (int(record["game_pk"]), int(record["pitcher_id"]))
        unique.setdefault(identity, dict(record))
    return list(unique.values())


async def apply_grades(
    *,
    projection_date: str,
    outcomes: Mapping[tuple[int, int], Mapping[str, Any]],
    db: Database = database,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Apply one official outcome to every model row for each pitcher-game."""
    records = await db.fetch_all(
        select(pitcher_k_predictions).where(
            pitcher_k_predictions.c.projection_date == projection_date
        )
    )
    now = _utc_now()
    updates: list[tuple[Mapping[str, Any], dict[str, Any]]] = []
    conflicts = []
    status_counts: Counter[str] = Counter()
    grade_fields = (
        "actual_ks",
        "actual_batters_faced",
        "actual_innings_pitched",
        "actual_pitch_count",
        "result_status",
        "started",
        "game_status",
        "grading_source",
        "grade_detail",
    )

    for record in records:
        identity = (int(record["game_pk"]), int(record["pitcher_id"]))
        outcome = outcomes.get(identity)
        if outcome is None:
            continue
        incoming_status = str(outcome.get("result_status") or "data_unavailable")
        if incoming_status not in RESULT_STATUSES:
            raise ValueError(f"Unknown Pitcher Ks result status: {incoming_status}")
        existing_status = _result_status(record)
        if existing_status in TERMINAL_RESULT_STATUSES and incoming_status not in TERMINAL_RESULT_STATUSES:
            continue
        existing_actual = _value(record, "actual_ks")
        incoming_actual = outcome.get("actual_ks")
        if (
            existing_actual is not None
            and incoming_actual is not None
            and int(existing_actual) != int(incoming_actual)
            and not force
        ):
            conflicts.append({
                "game_pk": identity[0],
                "pitcher_id": identity[1],
                "existing_actual_ks": int(existing_actual),
                "incoming_actual_ks": int(incoming_actual),
            })
            continue
        values = {field: outcome.get(field) for field in grade_fields}
        values["graded_at"] = (
            str(_value(record, "graded_at") or outcome.get("graded_at") or now)
            if incoming_status in TERMINAL_RESULT_STATUSES
            else None
        )
        changed = any(_value(record, key) != value for key, value in values.items())
        if changed:
            updates.append((record, values))
        status_counts[incoming_status] += 1

    if conflicts:
        raise ValueError(
            "Official Pitcher Ks outcomes conflict with stored grades. "
            "Review the stat correction and rerun with force only if intentional: "
            + json.dumps(conflicts, sort_keys=True)
        )

    if not dry_run:
        async with db.transaction():
            for record, values in updates:
                await db.execute(
                    pitcher_k_predictions.update()
                    .where(pitcher_k_predictions.c.id == record["id"])
                    .values(**values)
                )
    return {
        "projection_date": projection_date,
        "prediction_rows": len(records),
        "matched_rows": sum(status_counts.values()),
        "changed_rows": len(updates),
        "dry_run": dry_run,
        "status_counts": dict(sorted(status_counts.items())),
    }


async def fetch_ledger_summary(*, db: Database = database) -> dict[str, Any]:
    """Evaluate one preferred published snapshot per date and approach."""
    run_records = await db.fetch_all(
        select(pitcher_k_runs).where(pitcher_k_runs.c.is_evaluation == 1)
    )
    precedence = {"morning": 1, "afternoon": 2}
    selected: dict[tuple[str, str], Mapping[str, Any]] = {}
    for run in run_records:
        key = (str(run["projection_date"]), str(run["approach"]))
        candidate = (
            precedence.get(str(run["prediction_window"]), 0),
            str(run["generated_at"]),
        )
        current = selected.get(key)
        current_order = (
            precedence.get(str(current["prediction_window"]), 0),
            str(current["generated_at"]),
        ) if current else (-1, "")
        if candidate > current_order:
            selected[key] = run
    if not selected:
        return {"days_graded": 0, "approaches": {}, "model_versions": {}}

    run_ids = [run["run_id"] for run in selected.values()]
    prediction_records = await db.fetch_all(
        select(pitcher_k_predictions).where(
            pitcher_k_predictions.c.run_id.in_(run_ids)
        )
    )
    by_run: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for prediction in prediction_records:
        by_run[str(prediction["run_id"])].append(prediction)

    by_approach: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_version: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    graded_dates: set[str] = set()
    approach_dates: dict[str, set[str]] = defaultdict(set)
    version_dates: dict[str, set[str]] = defaultdict(set)
    for run in selected.values():
        predictions = by_run[str(run["run_id"])]
        approach = str(run["approach"])
        version = str(run["model_version"])
        by_approach[approach].extend(predictions)
        by_version[version].extend(predictions)
        if any(_result_status(row) == "graded" for row in predictions):
            projection_date = str(run["projection_date"])
            graded_dates.add(projection_date)
            approach_dates[approach].add(projection_date)
            version_dates[version].add(projection_date)

    approaches = {}
    for approach, rows in by_approach.items():
        metrics = evaluation_metrics(rows)
        metrics["days"] = len(approach_dates[approach])
        metrics["model_versions"] = sorted({
            str(run["model_version"])
            for run in selected.values()
            if run["approach"] == approach
        })
        approaches[approach] = metrics
    model_versions = {}
    for version, rows in by_version.items():
        metrics = evaluation_metrics(rows)
        metrics["days"] = len(version_dates[version])
        model_versions[version] = metrics
    return {
        "days_graded": len(graded_dates),
        "approaches": approaches,
        "model_versions": model_versions,
    }
