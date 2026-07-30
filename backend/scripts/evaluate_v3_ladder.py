"""Run the predeclared V3 development ladder without opening the final test."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import polars as pl


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from hit_model.benchmark import (
    file_sha256,
    git_commit,
    probability_metrics,
    ranked_top_n_rows,
    top_n_summary,
)
from hit_model.experiment_contract import (
    DEFAULT_CONTRACT_PATH,
    contract_fingerprint,
    load_experiment_contract,
)
from hit_model.v3_model import (
    FEATURE_LADDER,
    feature_coverage,
    feature_set,
    fit_game_model,
    prepare_v3_frame,
    to_v3_matrix,
    v3_recipe_fingerprint,
)
from ml_environment import (
    dataframe_fingerprint,
    dependency_fingerprint,
    json_fingerprint,
)


IDENTITY_COLUMNS = [
    "game_date",
    "game_id",
    "player_id",
    "player_name",
    "team",
    "opponent",
    "batting_order",
    "lineup_source",
    "projected_starter_probability",
    "final_starter",
    "final_batting_order",
    "pa_game",
    "got_hit",
]


def top_rate(predictions: pl.DataFrame, n: int) -> dict[str, Any]:
    selected = ranked_top_n_rows(predictions, n)
    hits = int(selected["got_hit"].cast(pl.Int64).sum())
    return {
        "rate": round(hits / selected.height, 6) if selected.height else None,
        "hits": hits,
        "picks": selected.height,
        "game_dates": selected["game_date"].n_unique(),
    }


def evaluate_experiment(
    df: pl.DataFrame,
    *,
    experiment_id: str,
    folds: list[dict[str, str]],
) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
    features = feature_set(experiment_id)
    predictions: list[pl.DataFrame] = []
    fold_metrics: list[dict[str, Any]] = []
    for fold in folds:
        test_start = fold["test_start"]
        test_end = fold["test_end"]
        train = df.filter(pl.col("game_date") < test_start)
        test = df.filter(
            (pl.col("game_date") >= test_start)
            & (pl.col("game_date") <= test_end)
        )
        if not train.height or not test.height:
            continue
        model = fit_game_model(train, experiment_id=experiment_id)
        scores = model.predict_proba(to_v3_matrix(test, features))[:, 1]
        identity = [
            column for column in IDENTITY_COLUMNS if column in test.columns
        ]
        fold_predictions = test.select(identity).with_columns(
            pl.Series("raw_probability", scores),
            pl.Series("probability", scores),
            pl.lit(experiment_id).alias("experiment_id"),
            pl.lit(fold["name"]).alias("fold"),
        )
        predictions.append(fold_predictions)
        y_true = test["got_hit"].cast(pl.Int8).to_numpy()
        fold_metrics.append({
            "fold": fold["name"],
            "test_start": test_start,
            "test_end": test_end,
            "n_train": train.height,
            "n_test": test.height,
            **probability_metrics(y_true, scores),
            "top5": top_rate(fold_predictions, 5),
            "top10": top_rate(fold_predictions, 10),
            "top15": top_rate(fold_predictions, 15),
        })
    if not predictions:
        raise RuntimeError(f"{experiment_id} produced no development predictions.")
    return pl.concat(predictions, how="vertical_relaxed"), fold_metrics


def promotion_check(
    *,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    gates: dict[str, Any],
) -> dict[str, Any]:
    top10_delta = (
        candidate["metrics"]["top_n"]["top10"]["rate"]
        - baseline["metrics"]["top_n"]["top10"]["rate"]
    )
    brier_delta = (
        candidate["metrics"]["probability"]["brier"]
        - baseline["metrics"]["probability"]["brier"]
    )
    log_loss_delta = (
        candidate["metrics"]["probability"]["log_loss"]
        - baseline["metrics"]["probability"]["log_loss"]
    )
    baseline_by_fold = {
        row["fold"]: row["top10"]["rate"]
        for row in baseline["fold_metrics"]
    }
    fold_deltas = {
        row["fold"]: round(
            row["top10"]["rate"] - baseline_by_fold[row["fold"]],
            6,
        )
        for row in candidate["fold_metrics"]
    }
    nonnegative = sum(delta >= 0 for delta in fold_deltas.values())
    checks = {
        "minimum_pooled_top10_delta": (
            top10_delta >= float(gates["minimum_pooled_top10_delta"])
        ),
        "minimum_nonnegative_development_folds": (
            nonnegative >= int(gates["minimum_nonnegative_development_folds"])
        ),
        "maximum_brier_increase": (
            brier_delta <= float(gates["maximum_brier_increase"])
        ),
        "maximum_log_loss_increase": (
            log_loss_delta <= float(gates["maximum_log_loss_increase"])
        ),
    }
    return {
        "passes": all(checks.values()),
        "checks": checks,
        "top10_delta": round(top10_delta, 6),
        "brier_delta": round(brier_delta, 6),
        "log_loss_delta": round(log_loss_delta, 6),
        "nonnegative_folds": nonnegative,
        "fold_top10_deltas": fold_deltas,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", nargs="+", required=True)
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT_PATH))
    parser.add_argument(
        "--experiments",
        nargs="+",
        default=list(FEATURE_LADDER),
        choices=list(FEATURE_LADDER),
    )
    parser.add_argument(
        "--artifact-dir",
        default=str(BACKEND_DIR / "backtest_results" / "v3_development"),
    )
    parser.add_argument(
        "--summary",
        default=str(
            BACKEND_DIR / "reports" / "v3" / "v3_development_ladder.json"
        ),
    )
    args = parser.parse_args()

    dataset_paths = [Path(value).resolve() for value in args.dataset]
    contract = load_experiment_contract(args.contract)
    raw_df = pl.concat(
        [pl.read_parquet(path) for path in dataset_paths],
        how="vertical_relaxed",
    )
    if set(raw_df["prediction_mode"].unique().to_list()) != {"projected"}:
        raise ValueError("V3 development requires one point-in-time projected dataset.")
    df = prepare_v3_frame(raw_df).sort("game_date")
    final = contract["evaluation"]["locked_final_backtest"]
    if df.filter(
        (pl.col("game_date") >= final["test_start"])
        & (pl.col("game_date") <= final["test_end"])
    ).height == 0:
        raise ValueError("Dataset does not contain the predeclared final window.")
    folds = contract["evaluation"]["development_folds"]
    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    experiments: dict[str, Any] = {}
    for experiment_id in args.experiments:
        predictions, fold_metrics = evaluate_experiment(
            df,
            experiment_id=experiment_id,
            folds=folds,
        )
        top_summary, selections = top_n_summary(
            predictions,
            bootstrap=contract["evaluation"]["bootstrap"],
        )
        y_true = predictions["got_hit"].cast(pl.Int8).to_numpy()
        probabilities = predictions["probability"].to_numpy()
        predictions_path = (
            artifact_dir / f"{experiment_id.lower()}_dev_predictions.parquet"
        )
        selections_path = (
            artifact_dir / f"{experiment_id.lower()}_dev_daily_topn.parquet"
        )
        predictions.write_parquet(predictions_path)
        selections.write_parquet(selections_path)
        experiments[experiment_id] = {
            "experiment_id": experiment_id,
            "feature_count": len(feature_set(experiment_id)),
            "feature_schema_sha256": json_fingerprint(
                feature_set(experiment_id)
            ),
            "recipe_sha256": v3_recipe_fingerprint(experiment_id),
            "oos_rows": predictions.height,
            "oos_game_dates": predictions["game_date"].n_unique(),
            "metrics": {
                "probability": probability_metrics(y_true, probabilities),
                "top_n": top_summary,
            },
            "fold_metrics": fold_metrics,
            "coverage": feature_coverage(df, feature_set(experiment_id)),
            "artifacts": {
                "predictions": {
                    "filename": predictions_path.name,
                    "sha256": file_sha256(predictions_path),
                    "rows": predictions.height,
                },
                "daily_topn": {
                    "filename": selections_path.name,
                    "sha256": file_sha256(selections_path),
                    "rows": selections.height,
                },
            },
        }

    if "E1" not in experiments:
        raise ValueError("E1 must be included as the corrected comparison baseline.")
    gates = contract["promotion_gates"]["shadow_entry"]
    for experiment_id, entry in experiments.items():
        entry["promotion_check"] = (
            {
                "passes": False,
                "reason": "E1 is the comparison baseline.",
            }
            if experiment_id == "E1"
            else promotion_check(
                baseline=experiments["E1"],
                candidate=entry,
                gates=gates,
            )
        )
    passing = [
        entry
        for experiment_id, entry in experiments.items()
        if experiment_id != "E1"
        and entry["promotion_check"].get("passes")
    ]
    passing.sort(
        key=lambda entry: (
            entry["promotion_check"]["top10_delta"],
            -entry["metrics"]["probability"]["brier"],
        ),
        reverse=True,
    )
    recommendation = (
        {
            "status": "candidate_available",
            "experiment_id": passing[0]["experiment_id"],
            "reason": "Highest pooled top-10 improvement among candidates passing every frozen shadow-entry gate.",
        }
        if passing
        else {
            "status": "no_candidate_passed",
            "experiment_id": None,
            "reason": "No feature ladder candidate passed every frozen shadow-entry gate.",
        }
    )
    dependency_sha, lock_sha = dependency_fingerprint()
    summary = {
        "report_version": "hit_v3_development_ladder_v1",
        "generated_at": datetime.now(timezone.utc).replace(
            microsecond=0
        ).isoformat(),
        "final_test_status": "sealed_not_evaluated",
        "contract_sha256": contract_fingerprint(contract),
        "code_commit": git_commit(REPO_ROOT),
        "dependency_fingerprint": dependency_sha,
        "dependency_lock_sha256": lock_sha,
        "dataset": {
            "files": [
                {
                    "path": str(path),
                    "sha256": file_sha256(path),
                    "bytes": path.stat().st_size,
                }
                for path in dataset_paths
            ],
            "frame_sha256": dataframe_fingerprint(df),
            "rows": df.height,
            "dates": {
                "start": str(df["game_date"].min()),
                "end": str(df["game_date"].max()),
            },
        },
        "development_folds": folds,
        "shadow_entry_gates": gates,
        "experiments": experiments,
        "recommendation": recommendation,
        "skipped": {
            "E6_weather": "No versioned historical weather source has been approved.",
            "E7_decomposed": "Evaluated only after the best enriched game-level feature set is identified.",
        },
    }
    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "ok",
        "summary": str(summary_path),
        "recommendation": recommendation,
        "top10": {
            experiment_id: entry["metrics"]["top_n"]["top10"]
            for experiment_id, entry in experiments.items()
        },
        "promotion_checks": {
            experiment_id: entry["promotion_check"]
            for experiment_id, entry in experiments.items()
        },
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
