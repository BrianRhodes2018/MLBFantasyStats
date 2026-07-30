"""Open the once-only locked final block for the frozen Hit Picks V3 candidate."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from typing import Any

import numpy as np
import polars as pl
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score


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
from hit_model.v3_calibration import (
    apply_lookup,
    fit_lookup,
    select_method,
)
from hit_model.v3_model import (
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


EXPERIMENT_ID = "E4"
IDENTITY_COLUMNS = [
    "game_date",
    "game_id",
    "player_id",
    "player_name",
    "team",
    "opponent",
    "batting_order",
    "lineup_source",
    "pitcher_throws",
    "projected_starter_probability",
    "final_starter",
    "final_batting_order",
    "pa_game",
    "got_hit",
]


def _verify_frozen_candidate(
    *,
    candidate: dict[str, Any],
    contract: dict[str, Any],
    development_report_path: Path,
) -> None:
    expected = {
        "experiment_contract_sha256": contract_fingerprint(contract),
        "feature_schema_sha256": json_fingerprint(feature_set(EXPERIMENT_ID)),
        "recipe_sha256": v3_recipe_fingerprint(EXPERIMENT_ID),
    }
    mismatches = [
        key
        for key, value in expected.items()
        if candidate.get(key) != value
    ]
    if candidate.get("status") != "frozen_before_locked_final":
        mismatches.append("status")
    if candidate.get("experiment_id") != EXPERIMENT_ID:
        mismatches.append("experiment_id")
    frozen_report_hash = (
        candidate.get("development_evidence", {})
        .get("report_sha256_at_freeze")
    )
    if file_sha256(development_report_path) != frozen_report_hash:
        mismatches.append("development_evidence.report_sha256_at_freeze")
    if mismatches:
        raise ValueError(
            "Frozen V3 candidate compatibility check failed: "
            + ", ".join(mismatches)
        )


def _commit_for_path(path: Path) -> str:
    try:
        relative = path.resolve().relative_to(REPO_ROOT.resolve())
        return subprocess.check_output(
            ["git", "log", "-1", "--format=%H", "--", str(relative)],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, ValueError, subprocess.CalledProcessError):
        return "unknown"


def _prediction_frame(
    test: pl.DataFrame,
    raw: np.ndarray,
    probability: np.ndarray,
    *,
    model_version: str,
) -> pl.DataFrame:
    identity = [
        column for column in IDENTITY_COLUMNS if column in test.columns
    ]
    return test.select(identity).with_columns(
        pl.Series("raw_probability", raw),
        pl.Series("probability", probability),
        pl.lit(model_version).alias("model_version"),
    )


def _paired_top10_interval(
    baseline: pl.DataFrame,
    candidate: pl.DataFrame,
    *,
    bootstrap: dict[str, Any],
) -> dict[str, Any]:
    base_daily = (
        ranked_top_n_rows(baseline, 10)
        .group_by("game_date")
        .agg(pl.col("got_hit").sum().alias("baseline_hits"))
    )
    candidate_daily = (
        ranked_top_n_rows(candidate, 10)
        .group_by("game_date")
        .agg(pl.col("got_hit").sum().alias("candidate_hits"))
    )
    paired = base_daily.join(
        candidate_daily,
        on="game_date",
        how="inner",
    ).sort("game_date")
    daily_delta = (
        paired["candidate_hits"].to_numpy().astype(float)
        - paired["baseline_hits"].to_numpy().astype(float)
    ) / 10.0
    iterations = int(bootstrap["iterations"])
    rng = np.random.default_rng(int(bootstrap["random_seed"]) + 50)
    samples = rng.integers(
        0,
        len(daily_delta),
        size=(iterations, len(daily_delta)),
    )
    estimates = daily_delta[samples].mean(axis=1)
    confidence = float(bootstrap["confidence_level"])
    alpha = 1.0 - confidence
    return {
        "delta": round(float(daily_delta.mean()), 6),
        "ci_low": round(float(np.quantile(estimates, alpha / 2.0)), 6),
        "ci_high": round(
            float(np.quantile(estimates, 1.0 - alpha / 2.0)),
            6,
        ),
        "paired_game_dates": len(daily_delta),
        "iterations": iterations,
        "cluster": "game_date",
        "seed": int(bootstrap["random_seed"]) + 50,
    }


def _calibration_table(frame: pl.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    probabilities = frame["probability"].to_numpy()
    outcomes = frame["got_hit"].cast(pl.Int8).to_numpy()
    for index in range(10):
        low = index / 10
        high = (index + 1) / 10
        mask = (probabilities >= low) & (
            probabilities <= high if index == 9 else probabilities < high
        )
        count = int(mask.sum())
        if not count:
            continue
        rows.append({
            "bucket": f"{low:.1f}-{high:.1f}",
            "rows": count,
            "mean_probability": round(float(probabilities[mask].mean()), 6),
            "actual_hit_rate": round(float(outcomes[mask].mean()), 6),
        })
    return rows


def _slice_metrics(
    frame: pl.DataFrame,
    column: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in frame[column].drop_nulls().unique().sort().to_list():
        part = frame.filter(pl.col(column) == value)
        y_true = part["got_hit"].cast(pl.Int8).to_numpy()
        probabilities = part["probability"].to_numpy()
        entry: dict[str, Any] = {
            column: value,
            "rows": part.height,
            "actual_hit_rate": round(float(y_true.mean()), 6),
            "mean_probability": round(float(probabilities.mean()), 6),
            "brier": round(
                float(brier_score_loss(y_true, probabilities)),
                6,
            ),
            "log_loss": round(
                float(log_loss(y_true, probabilities, labels=[0, 1])),
                6,
            ),
        }
        if len(np.unique(y_true)) > 1:
            entry["roc_auc"] = round(
                float(roc_auc_score(y_true, probabilities)),
                6,
            )
        rows.append(entry)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", nargs="+", required=True)
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT_PATH))
    parser.add_argument(
        "--candidate",
        default=str(
            BACKEND_DIR / "config" / "hit_model_v3_candidate.json"
        ),
    )
    parser.add_argument(
        "--development-report",
        default=str(
            BACKEND_DIR / "reports" / "v3" / "v3_development_ladder.json"
        ),
    )
    parser.add_argument(
        "--development-predictions",
        default=str(
            BACKEND_DIR
            / "backtest_results"
            / "v3_development"
            / "e4_dev_predictions.parquet"
        ),
    )
    parser.add_argument(
        "--artifact-dir",
        default=str(BACKEND_DIR / "backtest_results" / "v3_final"),
    )
    parser.add_argument(
        "--summary",
        default=str(
            BACKEND_DIR / "reports" / "v3" / "v3_locked_final.json"
        ),
    )
    parser.add_argument(
        "--calibration-output",
        default=str(
            BACKEND_DIR / "calibration" / "hit_gbm_v3_e4.json"
        ),
    )
    args = parser.parse_args()

    candidate_path = Path(args.candidate)
    development_report_path = Path(args.development_report)
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    contract = load_experiment_contract(args.contract)
    _verify_frozen_candidate(
        candidate=candidate,
        contract=contract,
        development_report_path=development_report_path,
    )

    dataset_paths = [Path(value).resolve() for value in args.dataset]
    df = prepare_v3_frame(
        pl.concat(
            [pl.read_parquet(path) for path in dataset_paths],
            how="vertical_relaxed",
        )
    ).sort("game_date")
    if (
        dataframe_fingerprint(df)
        != candidate["development_dataset_frame_sha256"]
    ):
        raise ValueError("Dataset fingerprint changed after candidate freeze.")
    final = contract["evaluation"]["locked_final_backtest"]
    train = df.filter(pl.col("game_date") < final["test_start"])
    test = df.filter(
        (pl.col("game_date") >= final["test_start"])
        & (pl.col("game_date") <= final["test_end"])
    )
    if not train.height or not test.height:
        raise ValueError("Locked final training or test window is empty.")

    start = perf_counter()
    baseline_model = fit_game_model(train, experiment_id="E1")
    baseline_raw = baseline_model.predict_proba(
        to_v3_matrix(test, feature_set("E1"))
    )[:, 1]
    baseline_runtime = perf_counter() - start

    start = perf_counter()
    v3_model = fit_game_model(train, experiment_id=EXPERIMENT_ID)
    v3_raw = v3_model.predict_proba(
        to_v3_matrix(test, feature_set(EXPERIMENT_ID))
    )[:, 1]
    v3_runtime = perf_counter() - start

    dev_predictions = pl.read_parquet(args.development_predictions)
    protocol = candidate["calibration_protocol"]
    method_fit = dev_predictions.filter(
        (pl.col("game_date") >= protocol["method_fit_window"]["start"])
        & (pl.col("game_date") <= protocol["method_fit_window"]["end"])
    )
    method_validation = dev_predictions.filter(
        (
            pl.col("game_date")
            >= protocol["method_validation_window"]["start"]
        )
        & (
            pl.col("game_date")
            <= protocol["method_validation_window"]["end"]
        )
    )
    selected_method, method_results = select_method(
        fit_raw=method_fit["raw_probability"].to_numpy(),
        fit_outcomes=method_fit["got_hit"].cast(pl.Int8).to_numpy(),
        validation_raw=method_validation["raw_probability"].to_numpy(),
        validation_outcomes=method_validation["got_hit"].cast(
            pl.Int8
        ).to_numpy(),
        maximum_brier_increase_vs_raw=float(
            protocol["maximum_allowed_brier_increase_vs_raw"]
        ),
    )
    full_fit = dev_predictions.filter(
        (
            pl.col("game_date")
            >= protocol["final_calibration_fit_window"]["start"]
        )
        & (
            pl.col("game_date")
            <= protocol["final_calibration_fit_window"]["end"]
        )
    )
    lookup = fit_lookup(
        selected_method,
        full_fit["raw_probability"].to_numpy(),
        full_fit["got_hit"].cast(pl.Int8).to_numpy(),
    )
    v3_probability = apply_lookup(v3_raw, lookup)

    baseline_predictions = _prediction_frame(
        test,
        baseline_raw,
        baseline_raw,
        model_version="hit_gbm_v2_corrected_e1",
    )
    v3_predictions = _prediction_frame(
        test,
        v3_raw,
        v3_probability,
        model_version=candidate["model_version"],
    )
    baseline_topn, baseline_selections = top_n_summary(
        baseline_predictions,
        bootstrap=contract["evaluation"]["bootstrap"],
    )
    v3_topn, v3_selections = top_n_summary(
        v3_predictions,
        bootstrap=contract["evaluation"]["bootstrap"],
    )
    y_true = test["got_hit"].cast(pl.Int8).to_numpy()

    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "e1_predictions": (
            artifact_dir / "e1_locked_final_predictions.parquet",
            baseline_predictions,
        ),
        "v3_predictions": (
            artifact_dir / "v3_e4_locked_final_predictions.parquet",
            v3_predictions,
        ),
        "e1_daily_topn": (
            artifact_dir / "e1_locked_final_daily_topn.parquet",
            baseline_selections,
        ),
        "v3_daily_topn": (
            artifact_dir / "v3_e4_locked_final_daily_topn.parquet",
            v3_selections,
        ),
    }
    artifact_manifest: dict[str, Any] = {}
    for name, (path, frame) in artifacts.items():
        frame.write_parquet(path)
        artifact_manifest[name] = {
            "filename": path.name,
            "sha256": file_sha256(path),
            "rows": frame.height,
        }

    dependency_sha, lock_sha = dependency_fingerprint()
    calibration_payload = {
        "calibration_version": "hit_gbm_v3_e4_calibration_v1",
        "model_version": candidate["model_version"],
        "method": selected_method,
        "base_model_recipe_sha256": candidate["recipe_sha256"],
        "feature_schema_sha256": candidate["feature_schema_sha256"],
        "dependency_fingerprint": dependency_sha,
        "dependency_lock_sha256": lock_sha,
        "candidate_config_sha256": file_sha256(candidate_path),
        "method_selection": {
            "fit_window": protocol["method_fit_window"],
            "validation_window": protocol["method_validation_window"],
            "validation_results": method_results,
            "selected": selected_method,
        },
        "calibration_fit": {
            **protocol["final_calibration_fit_window"],
            "n_pairs": full_fit.height,
        },
        "calibration_test": {
            "start": final["test_start"],
            "end": final["test_end"],
            "n_pairs": test.height,
            "raw": probability_metrics(y_true, v3_raw),
            "calibrated": probability_metrics(y_true, v3_probability),
        },
        "x": lookup["x"],
        "y": lookup["y"],
    }
    calibration_path = Path(args.calibration_output)
    calibration_path.parent.mkdir(parents=True, exist_ok=True)
    calibration_path.write_text(
        json.dumps(calibration_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    v3_with_slices = v3_predictions.with_columns(
        pl.col("game_date").str.slice(0, 7).alias("month"),
        (
            pl.col("batter_contact_missing").fill_null(1)
            + pl.col("pitcher_pitch_data_missing").fill_null(1)
            + pl.col("arsenal_matchup_missing").fill_null(1)
        ).alias("fallback_count")
        if "batter_contact_missing" in v3_predictions.columns
        else pl.lit(0).alias("fallback_count"),
    )
    # Slice-only feature columns live on the source test frame, so attach
    # them after the immutable prediction artifact is built.
    slice_columns = [
        column
        for column in (
            "batter_contact_missing",
            "pitcher_pitch_data_missing",
            "arsenal_matchup_missing",
        )
        if column in test.columns
    ]
    if slice_columns:
        v3_with_slices = v3_predictions.hstack(
            test.select(slice_columns)
        ).with_columns(
            pl.col("game_date").str.slice(0, 7).alias("month"),
            (
                pl.sum_horizontal(
                    [
                        pl.col(column).fill_null(1)
                        for column in slice_columns
                    ]
                )
                .cast(pl.Int8)
                .alias("fallback_count")
            ),
        )
    v3_with_slices = v3_with_slices.with_columns(
        pl.when(pl.col("fallback_count") == 0)
        .then(pl.lit("complete"))
        .when(pl.col("fallback_count") == 1)
        .then(pl.lit("partial"))
        .otherwise(pl.lit("fallback"))
        .alias("feature_coverage_tier")
    )

    summary = {
        "report_version": "hit_v3_locked_final_v1",
        "generated_at": (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        ),
        "final_test_status": "opened_once_after_candidate_freeze",
        "candidate_freeze_commit": _commit_for_path(candidate_path),
        "locked_evaluation_code_commit": git_commit(REPO_ROOT),
        "candidate_config_sha256": file_sha256(candidate_path),
        "contract_sha256": contract_fingerprint(contract),
        "dependency_fingerprint": dependency_sha,
        "dependency_lock_sha256": lock_sha,
        "dataset": {
            "frame_sha256": dataframe_fingerprint(df),
            "rows": df.height,
            "train_rows": train.height,
            "test_rows": test.height,
            "test_game_dates": test["game_date"].n_unique(),
            "locked_window": final,
            "files": [
                {
                    "name": path.name,
                    "sha256": file_sha256(path),
                    "bytes": path.stat().st_size,
                }
                for path in dataset_paths
            ],
        },
        "calibration": {
            "selected_method": selected_method,
            "method_selection_results": method_results,
            "fit_rows": full_fit.height,
            "artifact": {
                "filename": calibration_path.name,
                "sha256": file_sha256(calibration_path),
            },
        },
        "baseline_e1": {
            "metrics": {
                "probability": probability_metrics(y_true, baseline_raw),
                "top_n": baseline_topn,
            },
            "runtime_seconds": round(baseline_runtime, 3),
        },
        "candidate_e4": {
            "metrics": {
                "raw_probability": probability_metrics(y_true, v3_raw),
                "calibrated_probability": probability_metrics(
                    y_true,
                    v3_probability,
                ),
                "top_n": v3_topn,
            },
            "runtime_seconds": round(v3_runtime, 3),
            "runtime_multiple_vs_e1": round(
                v3_runtime / max(baseline_runtime, 1e-9),
                3,
            ),
            "paired_top10_delta": _paired_top10_interval(
                baseline_predictions,
                v3_predictions,
                bootstrap=contract["evaluation"]["bootstrap"],
            ),
            "calibration_table": _calibration_table(v3_predictions),
            "slices": {
                "lineup_source": _slice_metrics(
                    v3_with_slices,
                    "lineup_source",
                ),
                "batting_order": _slice_metrics(
                    v3_with_slices,
                    "batting_order",
                ),
                "pitcher_throws": _slice_metrics(
                    v3_with_slices,
                    "pitcher_throws",
                ),
                "month": _slice_metrics(v3_with_slices, "month"),
                "feature_coverage_tier": _slice_metrics(
                    v3_with_slices,
                    "feature_coverage_tier",
                ),
            },
        },
        "artifacts": artifact_manifest,
        "decision": {
            "shadow_entry": (
                "approved"
                if (
                    v3_topn["top10"]["rate"]
                    > baseline_topn["top10"]["rate"]
                    and (
                        probability_metrics(y_true, v3_probability)["brier"]
                        - probability_metrics(y_true, baseline_raw)["brier"]
                    )
                    <= float(
                        contract["promotion_gates"]["shadow_entry"][
                            "maximum_brier_increase"
                        ]
                    )
                    and (
                        probability_metrics(y_true, v3_probability)[
                            "log_loss"
                        ]
                        - probability_metrics(y_true, baseline_raw)[
                            "log_loss"
                        ]
                    )
                    <= float(
                        contract["promotion_gates"]["shadow_entry"][
                            "maximum_log_loss_increase"
                        ]
                    )
                    and (
                        v3_runtime / max(baseline_runtime, 1e-9)
                        <= float(
                            contract["promotion_gates"]["shadow_entry"][
                                "maximum_runtime_multiple"
                            ]
                        )
                    )
                )
                else "rejected"
            ),
            "primary_promotion": "not_eligible_until_live_shadow_minimums",
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
        "selected_calibration": selected_method,
        "e1_top10": baseline_topn["top10"],
        "v3_top10": v3_topn["top10"],
        "paired_delta": summary["candidate_e4"]["paired_top10_delta"],
        "probability": summary["candidate_e4"]["metrics"],
        "decision": summary["decision"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
