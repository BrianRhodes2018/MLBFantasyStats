"""
train_hit_model.py - Train and evaluate daily 1+ hit prediction models.

Phase 3 of the hit-prediction plan. Loads the per-batter-game dataset
produced by build_hit_dataset.py and compares models with WALK-FORWARD
validation: each test block is predicted by a model trained only on
strictly earlier dates, mimicking how the model would really be used
(train on the season so far, predict today's slate).

Models compared:
  - naive          — no ML. Rank hitters by season hit/PA among
                     top-5 lineup slots with an established sample.
                     The dumb-but-strong benchmark: if ML can't beat
                     this, the extra features aren't earning anything.
  - logistic       — L2 logistic regression (impute + scale). Linear,
                     interpretable coefficients.
  - gbm            — HistGradientBoostingClassifier. Handles missing
                     values natively and finds feature interactions;
                     kept shallow because ~20k rows is a small sample.

Primary product metric: top-N picks per day -> what fraction got a hit.
Also reports AUC / Brier / log-loss for model quality and calibration.

Example:
    python backend/train_hit_model.py --dataset backend/data/hit_dataset.parquet
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import polars as pl
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_DATASET = BACKEND_DIR / "data" / "hit_dataset.parquet"
DEFAULT_RESULTS_DIR = BACKEND_DIR / "backtest_results"

TOP_NS = (5, 10, 15)

# Feature columns fed to the models. Everything here is known BEFORE the
# game starts. Identifier/outcome columns are deliberately excluded.
FEATURES = [
    # opportunity / context
    "batting_order", "is_home",
    # platoon + matchup history
    "platoon_advantage", "vs_hand_pa", "vs_hand_hit_per_pa",
    "faced_pitcher_pa", "faced_pitcher_hit_per_pa",
    # park
    "park_runs_factor", "park_hr_factor",
    # batter form
    "season_pa", "season_hit_per_pa", "season_k_pct",
    "season_contact_rate", "season_woba",
    "last5_pa", "last5_hit_per_pa", "last5_k_pct", "last5_contact_rate", "last5_woba",
    "last10_pa", "last10_hit_per_pa", "last10_k_pct", "last10_contact_rate", "last10_woba",
    "last20_pa", "last20_hit_per_pa", "last20_k_pct", "last20_contact_rate", "last20_woba",
    "days_rest", "games_last7",
    # opposing starter
    "p_season_ip", "p_season_h_per_9", "p_season_whip", "p_season_fip",
    "p_season_k_pct", "p_season_bb_pct", "p_season_k_bb_pct", "p_season_hr_per_9",
    "p_season_starts",
    "p_last3_ip", "p_last3_h_per_9", "p_last3_whip", "p_last3_fip",
    "p_last3_k_pct", "p_last3_k_bb_pct",
    # opposing bullpen
    "opp_bullpen_ip", "opp_bullpen_h_per_9", "opp_bullpen_whip", "opp_bullpen_k_pct",
    # explicit interaction: contact hitter vs contact pitcher is the classic
    # 1+ hit spot; linear models can't see products without help.
    "batter_k_x_pitcher_k",
]

# Walk-forward test blocks (inclusive date ranges). Training data for each
# block is every row dated strictly BEFORE the block starts.
DEFAULT_FOLDS = [
    ("2026-05-16", "2026-05-31"),
    ("2026-06-01", "2026-06-15"),
    ("2026-06-16", "2026-07-03"),
]

# Feature groups for the ablation study: drop one group at a time and
# measure how much the model degrades. Groups that cost nothing when
# removed aren't earning their complexity.
FEATURE_GROUPS = {
    "context": ["batting_order", "is_home", "days_rest", "games_last7"],
    "platoon": ["platoon_advantage", "vs_hand_pa", "vs_hand_hit_per_pa"],
    "bvp": ["faced_pitcher_pa", "faced_pitcher_hit_per_pa"],
    "park": ["park_runs_factor", "park_hr_factor"],
    "batter_season": [
        "season_pa", "season_hit_per_pa", "season_k_pct",
        "season_contact_rate", "season_woba",
    ],
    "batter_recent": [
        "last5_pa", "last5_hit_per_pa", "last5_k_pct", "last5_contact_rate", "last5_woba",
        "last10_pa", "last10_hit_per_pa", "last10_k_pct", "last10_contact_rate", "last10_woba",
        "last20_pa", "last20_hit_per_pa", "last20_k_pct", "last20_contact_rate", "last20_woba",
    ],
    "pitcher_season": [
        "p_season_ip", "p_season_h_per_9", "p_season_whip", "p_season_fip",
        "p_season_k_pct", "p_season_bb_pct", "p_season_k_bb_pct", "p_season_hr_per_9",
        "p_season_starts",
    ],
    "pitcher_recent": [
        "p_last3_ip", "p_last3_h_per_9", "p_last3_whip", "p_last3_fip",
        "p_last3_k_pct", "p_last3_k_bb_pct",
    ],
    "bullpen": ["opp_bullpen_ip", "opp_bullpen_h_per_9", "opp_bullpen_whip", "opp_bullpen_k_pct"],
    "interaction": ["batter_k_x_pitcher_k"],
}


@dataclass
class FoldResult:
    name: str
    test_start: str
    test_end: str
    n_train: int
    n_test: int
    metrics: dict[str, Any]


def prepare_frame(df: pl.DataFrame) -> pl.DataFrame:
    """Model-ready transformations shared by training and daily prediction:
    the explicit K%-matchup interaction plus integer casts."""
    exprs = [
        (pl.col("season_k_pct") * pl.col("p_season_k_pct") / 100.0)
        .alias("batter_k_x_pitcher_k"),
        pl.col("is_home").cast(pl.Int8),
        pl.col("platoon_advantage").cast(pl.Int8),
    ]
    if "got_hit" in df.columns:
        exprs.append(pl.col("got_hit").cast(pl.Int8))
    return df.with_columns(exprs)


def load_dataset(path: Path) -> pl.DataFrame:
    df = pl.read_parquet(path)
    # Rows where the batter never actually batted (announced but replaced,
    # rain shortening, etc.) carry no usable label.
    df = df.filter(pl.col("pa_game") > 0)
    return prepare_frame(df)


def to_matrix(df: pl.DataFrame, features: list[str] = FEATURES) -> np.ndarray:
    """Feature matrix with None -> NaN, everything as float64."""
    return df.select(features).to_numpy().astype(np.float64)


def make_models() -> dict[str, Any]:
    return {
        "logistic": Pipeline([
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
            ("clf", LogisticRegression(max_iter=3000, C=1.0)),
        ]),
        "gbm": HistGradientBoostingClassifier(
            max_depth=3,
            learning_rate=0.06,
            max_iter=400,
            min_samples_leaf=60,
            l2_regularization=1.0,
            early_stopping=True,
            validation_fraction=0.15,
            random_state=7,
        ),
    }


def naive_scores(df: pl.DataFrame) -> np.ndarray:
    """
    The no-ML benchmark: season hit/PA, but only for hitters in the top
    five lineup slots with 100+ season PA. Everyone else scores -inf so
    they are never picked.
    """
    scores = (
        df.select(
            pl.when(
                (pl.col("batting_order") <= 5)
                & (pl.col("season_pa") >= 100)
                & pl.col("season_hit_per_pa").is_not_null()
            )
            .then(pl.col("season_hit_per_pa"))
            .otherwise(float("-inf"))
            .alias("score")
        )
        .to_numpy()
        .ravel()
    )
    return scores


def top_n_hit_rates(
    test_df: pl.DataFrame,
    scores: np.ndarray,
    top_ns: tuple[int, ...] = TOP_NS,
) -> dict[str, Any]:
    """For each day in the test block, pick the N highest-scored hitters
    and measure how often they actually got a hit."""
    scored = test_df.select(["game_date", "got_hit"]).with_columns(
        pl.Series("score", scores)
    )
    out: dict[str, Any] = {}
    for n in top_ns:
        picks = (
            scored.filter(pl.col("score").is_finite())
            .sort(["game_date", "score"], descending=[False, True])
            .group_by("game_date", maintain_order=True)
            .head(n)
        )
        out[f"top{n}_hit_rate"] = round(float(picks["got_hit"].mean()), 4) if picks.height else None
        out[f"top{n}_picks"] = picks.height
    return out


def probability_metrics(y_true: np.ndarray, probs: np.ndarray) -> dict[str, float]:
    return {
        "auc": round(float(roc_auc_score(y_true, probs)), 4),
        "brier": round(float(brier_score_loss(y_true, probs)), 4),
        "log_loss": round(float(log_loss(y_true, probs)), 4),
    }


def run_walk_forward(
    df: pl.DataFrame,
    folds: list[tuple[str, str]],
    features: list[str] = FEATURES,
    *,
    include_naive: bool = True,
    collect_probs: bool = False,
) -> tuple[dict[str, list[FoldResult]], dict[str, Any]]:
    """Returns (per-model fold results, pooled out-of-sample predictions).

    The pooled predictions (one probability per test row, from the model
    that had never seen that row's date) feed the calibration table.
    """
    results: dict[str, list[FoldResult]] = {}
    if include_naive:
        results["naive"] = []
    pooled_probs: dict[str, list[np.ndarray]] = {}
    pooled_truth: list[np.ndarray] = []

    for test_start, test_end in folds:
        train_df = df.filter(pl.col("game_date") < test_start)
        test_df = df.filter(
            (pl.col("game_date") >= test_start) & (pl.col("game_date") <= test_end)
        )
        if not train_df.height or not test_df.height:
            continue

        X_train, y_train = to_matrix(train_df, features), train_df["got_hit"].to_numpy()
        X_test, y_test = to_matrix(test_df, features), test_df["got_hit"].to_numpy()
        if collect_probs:
            pooled_truth.append(y_test)

        if include_naive:
            # Naive benchmark needs no training.
            naive = naive_scores(test_df)
            results["naive"].append(FoldResult(
                "naive", test_start, test_end, train_df.height, test_df.height,
                top_n_hit_rates(test_df, naive),
            ))

        for name, model in make_models().items():
            model.fit(X_train, y_train)
            probs = model.predict_proba(X_test)[:, 1]
            metrics = probability_metrics(y_test, probs)
            metrics.update(top_n_hit_rates(test_df, probs))
            results.setdefault(name, []).append(FoldResult(
                name, test_start, test_end, train_df.height, test_df.height, metrics,
            ))
            if collect_probs:
                pooled_probs.setdefault(name, []).append(probs)

    pooled = {}
    if collect_probs and pooled_truth:
        pooled["y_true"] = np.concatenate(pooled_truth)
        pooled["probs"] = {
            name: np.concatenate(chunks) for name, chunks in pooled_probs.items()
        }
    return results, pooled


def reliability_table(
    y_true: np.ndarray,
    probs: np.ndarray,
    edges: tuple[float, ...] = (0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75),
) -> list[dict[str, Any]]:
    """Bucket predicted probabilities and compare to actual hit rates.
    A calibrated model's 'predicted' and 'actual' columns match closely."""
    bounds = (float("-inf"),) + edges + (float("inf"),)
    rows = []
    for low, high in zip(bounds[:-1], bounds[1:]):
        mask = (probs >= low) & (probs < high)
        count = int(mask.sum())
        rows.append({
            "bucket": f"[{low:.2f}, {high:.2f})",
            "count": count,
            "avg_predicted": round(float(probs[mask].mean()), 4) if count else None,
            "actual_hit_rate": round(float(y_true[mask].mean()), 4) if count else None,
        })
    return rows


def run_ablation(
    df: pl.DataFrame,
    folds: list[tuple[str, str]],
) -> dict[str, Any]:
    """Drop each feature group and re-run the walk-forward. The drop in
    pooled top-10 hit rate / AUC is that group's earned contribution."""
    full_results, _ = run_walk_forward(df, folds, FEATURES, include_naive=False)
    full_pooled = {name: pooled_summary(frs) for name, frs in full_results.items()}

    ablation: dict[str, Any] = {"full": full_pooled}
    for group, columns in FEATURE_GROUPS.items():
        reduced = [f for f in FEATURES if f not in columns]
        results, _ = run_walk_forward(df, folds, reduced, include_naive=False)
        entry = {}
        for name, frs in results.items():
            pooled = pooled_summary(frs)
            entry[name] = {
                "top10_hit_rate": pooled.get("top10_hit_rate"),
                "auc": pooled.get("auc"),
                "delta_top10": round(
                    (pooled.get("top10_hit_rate") or 0)
                    - (full_pooled[name].get("top10_hit_rate") or 0), 4,
                ),
                "delta_auc": round(
                    (pooled.get("auc") or 0) - (full_pooled[name].get("auc") or 0), 4,
                ),
            }
        ablation[group] = entry
    return ablation


def pooled_summary(fold_results: list[FoldResult]) -> dict[str, Any]:
    """Weighted-average metrics across folds (weighted by pick/test counts)."""
    summary: dict[str, Any] = {}
    for n in TOP_NS:
        rate_key, picks_key = f"top{n}_hit_rate", f"top{n}_picks"
        total_picks = sum(fr.metrics.get(picks_key) or 0 for fr in fold_results)
        if total_picks:
            hits = sum(
                (fr.metrics[rate_key] or 0.0) * (fr.metrics.get(picks_key) or 0)
                for fr in fold_results
            )
            summary[rate_key] = round(hits / total_picks, 4)
            summary[picks_key] = total_picks
    for key in ("auc", "brier", "log_loss"):
        values = [(fr.metrics[key], fr.n_test) for fr in fold_results if key in fr.metrics]
        if values:
            total = sum(n for _, n in values)
            summary[key] = round(sum(v * n for v, n in values) / total, 4)
    return summary


def logistic_coefficients(df: pl.DataFrame) -> list[tuple[str, float]]:
    """Fit logistic on the full dataset and rank standardized coefficients —
    the direct answer to 'which parameters matter most (linearly)'."""
    model = make_models()["logistic"]
    model.fit(to_matrix(df), df["got_hit"].to_numpy())
    imputer: SimpleImputer = model.named_steps["impute"]
    names = list(FEATURES) + [
        f"{FEATURES[i]}_missing" for i in imputer.indicator_.features_
    ]
    coefs = model.named_steps["clf"].coef_[0]
    ranked = sorted(zip(names, coefs), key=lambda item: abs(item[1]), reverse=True)
    return [(name, round(float(coef), 4)) for name, coef in ranked]


def print_report(
    df: pl.DataFrame,
    results: dict[str, list[FoldResult]],
    coefficients: list[tuple[str, float]],
) -> dict[str, Any]:
    base_rate = float(df["got_hit"].mean())
    print("WALK-FORWARD RESULTS (train on past, predict future block)")
    print(f"dataset rows: {df.height}, base hit rate: {base_rate:.4f}\n")

    header = f"{'model':10s} {'fold':23s} {'AUC':>6s} {'Brier':>6s} " + " ".join(
        f"{'top' + str(n):>7s}" for n in TOP_NS
    )
    print(header)
    print("-" * len(header))
    for name, fold_results in results.items():
        for fr in fold_results:
            auc = fr.metrics.get("auc")
            brier = fr.metrics.get("brier")
            tops = " ".join(
                f"{(fr.metrics.get(f'top{n}_hit_rate') or 0) * 100:6.1f}%" for n in TOP_NS
            )
            print(
                f"{name:10s} {fr.test_start}..{fr.test_end}  "
                f"{auc if auc is not None else '  -  '!s:>6} "
                f"{brier if brier is not None else '  -  '!s:>6} {tops}"
            )
        pooled = pooled_summary(fold_results)
        tops = " ".join(
            f"{(pooled.get(f'top{n}_hit_rate') or 0) * 100:6.1f}%" for n in TOP_NS
        )
        print(f"{name:10s} {'POOLED':23s} {pooled.get('auc', '  -  ')!s:>6} "
              f"{pooled.get('brier', '  -  ')!s:>6} {tops}\n")

    print("TOP LOGISTIC COEFFICIENTS (standardized; + helps, - hurts)")
    for name, coef in coefficients[:15]:
        print(f"  {name:32s} {coef:+.4f}")

    return {
        "base_hit_rate": round(base_rate, 4),
        "pooled": {name: pooled_summary(frs) for name, frs in results.items()},
        "folds": {
            name: [
                {
                    "test_start": fr.test_start,
                    "test_end": fr.test_end,
                    "n_train": fr.n_train,
                    "n_test": fr.n_test,
                    **fr.metrics,
                }
                for fr in frs
            ]
            for name, frs in results.items()
        },
        "logistic_coefficients": coefficients,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Walk-forward evaluation of hit models.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help="Parquet from build_hit_dataset.py.")
    parser.add_argument("--output-json", help="Optional path for the JSON report.")
    parser.add_argument("--ablation", action="store_true", help="Also run the feature-group ablation study (slower).")
    args = parser.parse_args()

    df = load_dataset(Path(args.dataset))
    results, pooled_preds = run_walk_forward(df, DEFAULT_FOLDS, collect_probs=True)
    coefficients = logistic_coefficients(df)
    report = print_report(df, results, coefficients)

    if pooled_preds:
        report["calibration"] = {}
        for name, probs in pooled_preds["probs"].items():
            table = reliability_table(pooled_preds["y_true"], probs)
            report["calibration"][name] = table
            print(f"\nCALIBRATION — {name} (pooled out-of-sample predictions)")
            print(f"{'bucket':16s} {'n':>6s} {'predicted':>10s} {'actual':>8s}")
            for row in table:
                if not row["count"]:
                    continue
                print(
                    f"{row['bucket']:16s} {row['count']:>6d} "
                    f"{row['avg_predicted']:>10.3f} {row['actual_hit_rate']:>8.3f}"
                )

    if args.ablation:
        ablation = run_ablation(df, DEFAULT_FOLDS)
        report["ablation"] = ablation
        print("\nABLATION (change in pooled metrics when the group is REMOVED;")
        print("negative delta = the group was helping)")
        print(f"{'group removed':16s} {'logistic top10':>15s} {'d_top10':>8s} {'d_auc':>7s}   {'gbm top10':>10s} {'d_top10':>8s} {'d_auc':>7s}")
        for group in FEATURE_GROUPS:
            lg, gb = ablation[group]["logistic"], ablation[group]["gbm"]
            print(
                f"{group:16s} {lg['top10_hit_rate'] * 100:>14.1f}% {lg['delta_top10'] * 100:>+7.1f}% {lg['delta_auc']:>+7.3f}   "
                f"{gb['top10_hit_rate'] * 100:>9.1f}% {gb['delta_top10'] * 100:>+7.1f}% {gb['delta_auc']:>+7.3f}"
            )

    report["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    report["dataset"] = str(args.dataset)
    report["features"] = FEATURES
    report["folds_config"] = DEFAULT_FOLDS

    if args.output_json:
        output_path = Path(args.output_json)
    else:
        DEFAULT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = DEFAULT_RESULTS_DIR / f"hit_model_walkforward_{stamp}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nSaved JSON: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
