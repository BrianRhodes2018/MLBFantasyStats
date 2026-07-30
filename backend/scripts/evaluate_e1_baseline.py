"""Evaluate the point-in-time corrected V2 E1 baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import polars as pl


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from hit_model.benchmark import (
    file_sha256,
    git_commit,
    probability_metrics,
    top_n_summary,
)
from hit_model.experiment_contract import (
    DEFAULT_CONTRACT_PATH,
    baseline_folds,
    contract_fingerprint,
    load_experiment_contract,
)
from ml_environment import (
    dataframe_fingerprint,
    dependency_fingerprint,
    json_fingerprint,
)
from train_hit_model import (
    FEATURES,
    load_dataset,
    model_recipe_fingerprint,
    run_walk_forward,
)


def lineup_accuracy(df: pl.DataFrame) -> dict:
    if "prediction_mode" not in df.columns:
        return {}
    projected = df.filter(pl.col("prediction_mode") == "projected")
    if not projected.height:
        return {}
    starters = projected.filter(pl.col("final_starter"))
    slot_error = (
        (pl.col("projected_batting_order") - pl.col("final_batting_order"))
        .abs()
        .mean()
    )
    return {
        "projected_candidates": projected.height,
        "game_dates": projected["game_date"].n_unique(),
        "final_starter_rate": round(
            float(projected["final_starter"].mean()),
            6,
        ),
        "zero_pa_rate": round(float((projected["pa_game"] == 0).mean()), 6),
        "exact_slot_rate_given_starter": round(
            float(
                (
                    starters["projected_batting_order"]
                    == starters["final_batting_order"]
                ).mean()
            ),
            6,
        ) if starters.height else None,
        "mean_absolute_slot_error_given_starter": round(
            float(starters.select(slot_error).item()),
            6,
        ) if starters.height else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT_PATH))
    parser.add_argument(
        "--artifact-dir",
        default=str(BACKEND_DIR / "backtest_results" / "v3_baseline"),
    )
    parser.add_argument(
        "--summary",
        default=str(BACKEND_DIR / "reports" / "v3" / "e1_baseline_summary.json"),
    )
    args = parser.parse_args()

    dataset_path = Path(args.dataset).resolve()
    contract = load_experiment_contract(args.contract)
    df = load_dataset([dataset_path], include_zero_pa=True)
    modes = df["prediction_mode"].unique().to_list()
    if len(modes) != 1 or modes[0] not in {"official", "projected"}:
        raise ValueError("E1 dataset must contain exactly one supported prediction mode.")
    prediction_mode = modes[0]
    results, pooled = run_walk_forward(
        df,
        baseline_folds(contract),
        include_naive=True,
        collect_probs=True,
    )
    rows: pl.DataFrame = pooled["rows"]
    raw = pooled["probs"]["gbm"]
    predictions = rows.with_columns(
        pl.Series("raw_probability", raw),
        pl.Series("probability", raw),
    )
    top_summary, selections = top_n_summary(
        predictions,
        bootstrap=contract["evaluation"]["bootstrap"],
    )

    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = artifact_dir / f"e1_{prediction_mode}_oos_predictions.parquet"
    selections_path = artifact_dir / f"e1_{prediction_mode}_daily_topn.parquet"
    predictions.write_parquet(predictions_path)
    selections.write_parquet(selections_path)

    dependency_sha, lock_sha = dependency_fingerprint()
    final = contract["evaluation"]["locked_final_backtest"]
    final_rows = predictions.filter(
        (pl.col("game_date") >= final["test_start"])
        & (pl.col("game_date") <= final["test_end"])
    )
    summary = {
        "benchmark_version": "v2_corrected_e1_v1",
        "model_version": "hit_gbm_v2_corrected_e1",
        "probability_status": "uncalibrated_experimental",
        "prediction_mode": prediction_mode,
        "contract_sha256": contract_fingerprint(contract),
        "code_commit": git_commit(REPO_ROOT),
        "base_model_recipe_sha256": model_recipe_fingerprint(),
        "feature_schema_sha256": json_fingerprint(FEATURES),
        "dependency_fingerprint": dependency_sha,
        "dependency_lock_sha256": lock_sha,
        "dataset": {
            "name": dataset_path.name,
            "sha256": file_sha256(dataset_path),
            "frame_sha256": dataframe_fingerprint(df),
            "bytes": dataset_path.stat().st_size,
            "rows": df.height,
        },
        "folds": [
            {"test_start": start, "test_end": end}
            for start, end in baseline_folds(contract)
        ],
        "oos_rows": predictions.height,
        "oos_game_dates": predictions["game_date"].n_unique(),
        "metrics": {
            "raw": probability_metrics(
                predictions["got_hit"].cast(pl.Int8).to_numpy(),
                raw,
            ),
            "top_n": top_summary,
        },
        "lineup_accuracy": lineup_accuracy(df),
        "hybrid_reconstruction": {
            "status": "prospective_only",
            "reason": (
                "Final MLB game feeds do not retain the timestamp when each "
                "official lineup became available. Live immutable morning and "
                "afternoon snapshots will provide honest hybrid evaluation."
            ),
        },
        "locked_final_baseline": {
            "window": final,
            "rows": final_rows.height,
            "game_dates": final_rows["game_date"].n_unique(),
        },
        "fold_metrics": [
            {
                "test_start": fold.test_start,
                "test_end": fold.test_end,
                "n_train": fold.n_train,
                "n_test": fold.n_test,
                **fold.metrics,
            }
            for fold in results["gbm"]
        ],
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
        "notes": [
            "Projected lineups use only prior dates in the production 14-day formula.",
            "Park factors use the completed prior-season Savant snapshot.",
            "PA=0 projected players remain negative product outcomes.",
            "Probabilities are intentionally uncalibrated until an E1-specific calibrator is fit.",
        ],
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
        "rows": summary["oos_rows"],
        "top10": summary["metrics"]["top_n"]["top10"],
        "lineup_accuracy": summary["lineup_accuracy"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
