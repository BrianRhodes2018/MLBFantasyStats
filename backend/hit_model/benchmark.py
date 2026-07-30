"""Reproducible benchmark-package utilities for V2 and V3 experiments."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import polars as pl
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from hit_calibration import apply_calibration, load_calibration
from hit_model.experiment_contract import (
    baseline_folds,
    contract_fingerprint,
)
from ml_environment import (
    dataframe_fingerprint,
    dependency_fingerprint,
    json_fingerprint,
)
from train_hit_model import (
    FEATURES,
    model_recipe_fingerprint,
    run_walk_forward,
)


TOP_NS = (5, 10, 15)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit(repo_root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def probability_metrics(y_true: np.ndarray, probabilities: np.ndarray) -> dict[str, float]:
    return {
        "roc_auc": round(float(roc_auc_score(y_true, probabilities)), 6),
        "brier": round(float(brier_score_loss(y_true, probabilities)), 6),
        "log_loss": round(float(log_loss(y_true, probabilities)), 6),
    }


def ranked_top_n_rows(
    predictions: pl.DataFrame,
    n: int,
) -> pl.DataFrame:
    """Production-equivalent ranking with raw score as deterministic tie-break."""
    return (
        predictions.sort(
            ["game_date", "probability", "raw_probability", "player_id"],
            descending=[False, True, True, False],
        )
        .group_by("game_date", maintain_order=True)
        .head(n)
        .with_columns(
            pl.col("game_date").cum_count().over("game_date").alias("rank"),
            pl.lit(n).alias("top_n"),
        )
    )


def clustered_rate_interval(
    daily: pl.DataFrame,
    *,
    iterations: int,
    confidence_level: float,
    seed: int,
) -> dict[str, Any]:
    """Bootstrap whole game dates, retaining every pick within each date."""
    if not daily.height:
        raise ValueError("Cannot bootstrap an empty daily table.")
    hits = daily["hits"].to_numpy().astype(float)
    picks = daily["picks"].to_numpy().astype(float)
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(hits), size=(iterations, len(hits)))
    sampled_hits = hits[indices].sum(axis=1)
    sampled_picks = picks[indices].sum(axis=1)
    rates = np.divide(sampled_hits, sampled_picks)
    alpha = 1.0 - confidence_level
    observed = float(hits.sum() / picks.sum())
    standard_error = float(rates.std(ddof=1))
    z_alpha = NormalDist().inv_cdf(1.0 - alpha / 2.0)
    z_power = NormalDist().inv_cdf(0.80)
    return {
        "rate": round(observed, 6),
        "hits": int(hits.sum()),
        "picks": int(picks.sum()),
        "game_dates": len(hits),
        "ci_low": round(float(np.quantile(rates, alpha / 2.0)), 6),
        "ci_high": round(float(np.quantile(rates, 1.0 - alpha / 2.0)), 6),
        "clustered_standard_error": round(standard_error, 6),
        "minimum_detectable_improvement_80pct_power": round(
            float((z_alpha + z_power) * standard_error),
            6,
        ),
        "iterations": iterations,
        "confidence_level": confidence_level,
        "seed": seed,
        "mde_method": (
            "(two-sided normal critical value + 80% power critical value) "
            "* date-cluster bootstrap standard error"
        ),
    }


def top_n_summary(
    predictions: pl.DataFrame,
    *,
    bootstrap: dict[str, Any],
) -> tuple[dict[str, Any], pl.DataFrame]:
    summaries: dict[str, Any] = {}
    selections: list[pl.DataFrame] = []
    for offset, n in enumerate(TOP_NS):
        selected = ranked_top_n_rows(predictions, n)
        daily = (
            selected.group_by("game_date", maintain_order=True)
            .agg(
                pl.col("got_hit").cast(pl.Int64).sum().alias("hits"),
                pl.len().alias("picks"),
            )
            .sort("game_date")
        )
        summaries[f"top{n}"] = clustered_rate_interval(
            daily,
            iterations=int(bootstrap["iterations"]),
            confidence_level=float(bootstrap["confidence_level"]),
            seed=int(bootstrap["random_seed"]) + offset,
        )
        selections.append(selected)
    return summaries, pl.concat(selections, how="diagonal_relaxed")


def build_v2_benchmark_package(
    *,
    df: pl.DataFrame,
    dataset_paths: list[Path],
    contract: dict[str, Any],
    output_dir: Path,
    repo_root: Path,
) -> dict[str, Any]:
    folds = baseline_folds(contract)
    results, pooled = run_walk_forward(
        df,
        folds,
        include_naive=True,
        collect_probs=True,
    )
    rows: pl.DataFrame = pooled["rows"]
    raw = pooled["probs"]["gbm"]
    calibrator = load_calibration()
    if calibrator is None:
        raise RuntimeError("The committed V2 calibration bundle is missing.")
    calibrated = apply_calibration(raw, calibrator)
    predictions = rows.with_columns(
        pl.Series("raw_probability", raw),
        pl.Series("probability", calibrated),
    )
    if "game_id" in predictions.columns:
        predictions = predictions.rename({"game_id": "game_pk"})

    top_summary, selections = top_n_summary(
        predictions,
        bootstrap=contract["evaluation"]["bootstrap"],
    )
    y_true = predictions["got_hit"].cast(pl.Int8).to_numpy()
    calibration_valid_from = (
        (calibrator.get("calibration_test") or {}).get("start")
        or "2025-07-01"
    )
    calibrated_evaluation = predictions.filter(
        pl.col("game_date") >= calibration_valid_from
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = output_dir / "v2_oos_predictions.parquet"
    selections_path = output_dir / "v2_daily_topn.parquet"
    predictions.write_parquet(predictions_path)
    selections.write_parquet(selections_path)

    dependency_sha, lock_sha = dependency_fingerprint()
    dataset_manifest = [
        {
            "name": path.name,
            "sha256": file_sha256(path),
            "bytes": path.stat().st_size,
        }
        for path in dataset_paths
    ]
    final = contract["evaluation"]["locked_final_backtest"]
    final_rows = predictions.filter(
        (pl.col("game_date") >= final["test_start"])
        & (pl.col("game_date") <= final["test_end"])
    )
    summary = {
        "benchmark_version": "v2_oos_package_v1",
        "model_version": "hit_gbm_v2_cal",
        "contract_sha256": contract_fingerprint(contract),
        "code_commit": git_commit(repo_root),
        "base_model_recipe_sha256": model_recipe_fingerprint(),
        "feature_schema_sha256": json_fingerprint(FEATURES),
        "dependency_fingerprint": dependency_sha,
        "dependency_lock_sha256": lock_sha,
        "datasets": dataset_manifest,
        "dataset_frame_sha256": dataframe_fingerprint(df),
        "folds": [
            {"test_start": start, "test_end": end}
            for start, end in folds
        ],
        "rows": predictions.height,
        "game_dates": predictions["game_date"].n_unique(),
        "metrics": {
            "raw": probability_metrics(y_true, raw),
            "calibrated_untouched": {
                "valid_from": calibration_valid_from,
                "rows": calibrated_evaluation.height,
                **probability_metrics(
                    calibrated_evaluation["got_hit"].cast(pl.Int8).to_numpy(),
                    calibrated_evaluation["probability"].to_numpy(),
                ),
            },
            "top_n": top_summary,
        },
        "locked_final_baseline": {
            "window": final,
            "rows": final_rows.height,
            "game_dates": final_rows["game_date"].n_unique(),
        },
        "artifacts": {
            "predictions": {
                "filename": predictions_path.name,
                "sha256": file_sha256(predictions_path),
                "bytes": predictions_path.stat().st_size,
                "rows": predictions.height,
            },
            "daily_topn": {
                "filename": selections_path.name,
                "sha256": file_sha256(selections_path),
                "bytes": selections_path.stat().st_size,
                "rows": selections.height,
            },
        },
        "determinism_key": hashlib.sha256(
            (
                file_sha256(predictions_path)
                + file_sha256(selections_path)
                + contract_fingerprint(contract)
            ).encode("ascii")
        ).hexdigest(),
        "notes": [
            "Large row-level artifacts remain in gitignored backtest_results.",
            "Top-N uncertainty resamples complete game dates, not individual hitters.",
            "Raw probability is the deterministic secondary sort for flat isotonic ties.",
            "Calibrated probability metrics exclude dates used to fit the V2 curve.",
        ],
    }
    summary["fold_metrics"] = {
        model: [
            {
                "test_start": fold.test_start,
                "test_end": fold.test_end,
                "n_train": fold.n_train,
                "n_test": fold.n_test,
                **fold.metrics,
            }
            for fold in fold_results
        ]
        for model, fold_results in results.items()
    }
    return summary
