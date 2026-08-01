"""Persistence and read models for Pitcher Ks projection runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, Optional
from uuid import UUID

from databases import Database
from sqlalchemy import and_, desc, select

from database import database
from models import pitcher_k_predictions, pitcher_k_runs
from .modeling import APPROACH_ORDER


APPROACHES = set(APPROACH_ORDER)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_uuid(value: str, field: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a UUID string.") from exc


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


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
    return {
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
        "actual_ks": record["actual_ks"],
        "actual_batters_faced": record["actual_batters_faced"],
        "graded_at": record["graded_at"],
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
    payload["predictions"] = [_prediction_payload(record) for record in prediction_records]
    return payload


async def fetch_latest_approach(
    approach: str,
    *,
    db: Database = database,
) -> Optional[dict[str, Any]]:
    if approach not in APPROACHES:
        raise ValueError(f"Unknown Pitcher Ks approach: {approach}")
    run = await db.fetch_one(
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
        .limit(1)
    )
    if run is None:
        return None
    return await _fetch_run_predictions(run, db=db)


async def fetch_latest_comparison(*, db: Database = database) -> Optional[dict[str, Any]]:
    latest = await db.fetch_one(
        select(pitcher_k_runs)
        .where(pitcher_k_runs.c.is_public == 1)
        .order_by(
            desc(pitcher_k_runs.c.projection_date),
            desc(pitcher_k_runs.c.generated_at),
        )
        .limit(1)
    )
    if latest is None:
        return None
    run_records = await db.fetch_all(
        select(pitcher_k_runs).where(
            and_(
                pitcher_k_runs.c.comparison_group_id == latest["comparison_group_id"],
                pitcher_k_runs.c.is_public == 1,
            )
        )
    )
    by_approach = {record["approach"]: record for record in run_records}
    if set(by_approach) != APPROACHES:
        return None

    approach_payloads = {
        approach: await _fetch_run_predictions(by_approach[approach], db=db)
        for approach in APPROACH_ORDER
    }
    rows_by_identity: dict[tuple[int, int], dict[str, Any]] = {}
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
                    "actual_ks",
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
                )
            }
    rows = []
    for row in rows_by_identity.values():
        if not all(approach in row for approach in APPROACH_ORDER):
            continue
        estimates = [float(row[approach]["projected_ks"]) for approach in APPROACH_ORDER]
        row["model_spread"] = round(max(estimates) - min(estimates), 2)
        rows.append(row)
    rows.sort(key=lambda row: (-max(row[a]["projected_ks"] for a in APPROACH_ORDER), row["pitcher_name"]))
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
        "rows": rows,
    }
