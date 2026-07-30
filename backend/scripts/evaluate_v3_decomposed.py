"""Evaluate the predeclared decomposed V3 architecture on development folds.

This command deliberately reads the locked-final rows only as part of the
dataset fingerprint. It trains and scores exclusively inside the development
folds declared before V3 results existed.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from time import perf_counter
from typing import Any

import polars as pl


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from hit_model.benchmark import (
    file_sha256,
    probability_metrics,
    top_n_summary,
)
from hit_model.experiment_contract import (
    DEFAULT_CONTRACT_PATH,
    load_experiment_contract,
)
from hit_model.v3_model import (
    bundle_metadata,
    decomposed_probabilities,
    feature_coverage,
    fit_opportunity_model,
    fit_per_pa_model,
    opportunity_feature_set,
    per_pa_feature_set,
    prepare_v3_frame,
    v3_recipe_fingerprint,
)
from ml_environment import json_fingerprint
from scripts.evaluate_v3_ladder import IDENTITY_COLUMNS, promotion_check, top_rate


EXPERIMENT_ID = "E7"
SOURCE_FEATURE_SET = "E4"


def evaluate(
    df: pl.DataFrame,
    *,
    folds: list[dict[str, str]],
) -> tuple[pl.DataFrame, list[dict[str, Any]], float]:
    predictions: list[pl.DataFrame] = []
    fold_metrics: list[dict[str, Any]] = []
    started = perf_counter()
    for fold in folds:
        train = df.filter(pl.col("game_date") < fold["test_start"])
        test = df.filter(
            (pl.col("game_date") >= fold["test_start"])
            & (pl.col("game_date") <= fold["test_end"])
        )
        if not train.height or not test.height:
            continue
        opportunity_model = fit_opportunity_model(train)
        per_pa_model = fit_per_pa_model(
            train,
            experiment_id=SOURCE_FEATURE_SET,
        )
        scores = decomposed_probabilities(
            opportunity_model=opportunity_model,
            per_pa_model=per_pa_model,
            df=test,
            experiment_id=SOURCE_FEATURE_SET,
        )
        identity = [
            column for column in IDENTITY_COLUMNS if column in test.columns
        ]
        fold_predictions = test.select(identity).with_columns(
            pl.Series("raw_probability", scores),
            pl.Series("probability", scores),
            pl.lit(EXPERIMENT_ID).alias("experiment_id"),
            pl.lit(fold["name"]).alias("fold"),
        )
        predictions.append(fold_predictions)
        y_true = test["got_hit"].cast(pl.Int8).to_numpy()
        fold_metrics.append({
            "fold": fold["name"],
            "test_start": fold["test_start"],
            "test_end": fold["test_end"],
            "n_train": train.height,
            "n_test": test.height,
            **probability_metrics(y_true, scores),
            "top5": top_rate(fold_predictions, 5),
            "top10": top_rate(fold_predictions, 10),
            "top15": top_rate(fold_predictions, 15),
        })
    if not predictions:
        raise RuntimeError("E7 produced no development predictions.")
    return (
        pl.concat(predictions, how="vertical_relaxed"),
        fold_metrics,
        perf_counter() - started,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", nargs="+", required=True)
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT_PATH))
    parser.add_argument(
        "--summary",
        default=str(
            BACKEND_DIR / "reports" / "v3" / "v3_development_ladder.json"
        ),
    )
    parser.add_argument(
        "--artifact-dir",
        default=str(BACKEND_DIR / "backtest_results" / "v3_development"),
    )
    args = parser.parse_args()

    summary_path = Path(args.summary)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("final_test_status") != "sealed_not_evaluated":
        raise ValueError("E7 development evaluation requires a sealed final test.")
    contract = load_experiment_contract(args.contract)
    paths = [Path(value).resolve() for value in args.dataset]
    df = prepare_v3_frame(
        pl.concat(
            [pl.read_parquet(path) for path in paths],
            how="vertical_relaxed",
        )
    ).sort("game_date")
    predictions, fold_metrics, runtime_seconds = evaluate(
        df,
        folds=contract["evaluation"]["development_folds"],
    )
    top_summary, selections = top_n_summary(
        predictions,
        bootstrap=contract["evaluation"]["bootstrap"],
    )
    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = artifact_dir / "e7_dev_predictions.parquet"
    selections_path = artifact_dir / "e7_dev_daily_topn.parquet"
    predictions.write_parquet(predictions_path)
    selections.write_parquet(selections_path)

    features = list(
        dict.fromkeys(
            [
                *opportunity_feature_set(),
                *per_pa_feature_set(SOURCE_FEATURE_SET),
            ]
        )
    )
    y_true = predictions["got_hit"].cast(pl.Int8).to_numpy()
    entry: dict[str, Any] = {
        "experiment_id": EXPERIMENT_ID,
        "architecture": "decomposed_opportunity_per_pa",
        "source_feature_set": SOURCE_FEATURE_SET,
        "feature_count": len(features),
        "feature_schema_sha256": json_fingerprint(features),
        "recipe_sha256": v3_recipe_fingerprint(
            SOURCE_FEATURE_SET,
            architecture="decomposed_opportunity_per_pa",
        ),
        "oos_rows": predictions.height,
        "oos_game_dates": predictions["game_date"].n_unique(),
        "runtime_seconds": round(runtime_seconds, 3),
        "metrics": {
            "probability": probability_metrics(
                y_true,
                predictions["probability"].to_numpy(),
            ),
            "top_n": top_summary,
        },
        "fold_metrics": fold_metrics,
        "coverage": feature_coverage(df, features),
        "bundle_preview": bundle_metadata(
            df.filter(pl.col("game_date") <= "2026-05-31"),
            experiment_id=SOURCE_FEATURE_SET,
            architecture="decomposed_opportunity_per_pa",
        ).__dict__,
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
    entry["promotion_check"] = promotion_check(
        baseline=summary["experiments"]["E1"],
        candidate=entry,
        gates=contract["promotion_gates"]["shadow_entry"],
    )
    summary["experiments"][EXPERIMENT_ID] = entry

    passing = [
        value
        for key, value in summary["experiments"].items()
        if key != "E1" and value["promotion_check"].get("passes")
    ]
    passing.sort(
        key=lambda value: (
            value["promotion_check"]["top10_delta"],
            -value["metrics"]["probability"]["brier"],
        ),
        reverse=True,
    )
    summary["recommendation"] = (
        {
            "status": "candidate_available",
            "experiment_id": passing[0]["experiment_id"],
            "reason": (
                "Highest pooled top-10 improvement among feature and "
                "architecture candidates passing every frozen entry gate."
            ),
        }
        if passing
        else {
            "status": "no_candidate_passed",
            "experiment_id": None,
            "reason": "No candidate passed every frozen shadow-entry gate.",
        }
    )
    summary["generated_at"] = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )
    summary["skipped"].pop("E7_decomposed", None)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "ok",
        "summary": str(summary_path),
        "e7_top10": entry["metrics"]["top_n"]["top10"],
        "promotion_check": entry["promotion_check"],
        "recommendation": summary["recommendation"],
        "runtime_seconds": entry["runtime_seconds"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
