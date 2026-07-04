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


@dataclass
class FoldResult:
    name: str
    test_start: str
    test_end: str
    n_train: int
    n_test: int
    metrics: dict[str, Any]


def load_dataset(path: Path) -> pl.DataFrame:
    df = pl.read_parquet(path)
    # Rows where the batter never actually batted (announced but replaced,
    # rain shortening, etc.) carry no usable label.
    df = df.filter(pl.col("pa_game") > 0)
    df = df.with_columns(
        (pl.col("season_k_pct") * pl.col("p_season_k_pct") / 100.0)
        .alias("batter_k_x_pitcher_k"),
        pl.col("is_home").cast(pl.Int8),
        pl.col("platoon_advantage").cast(pl.Int8),
        pl.col("got_hit").cast(pl.Int8),
    )
    return df


def to_matrix(df: pl.DataFrame) -> np.ndarray:
    """Feature matrix with None -> NaN, everything as float64."""
    return df.select(FEATURES).to_numpy().astype(np.float64)


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
) -> dict[str, list[FoldResult]]:
    results: dict[str, list[FoldResult]] = {"naive": [], "logistic": [], "gbm": []}

    for test_start, test_end in folds:
        train_df = df.filter(pl.col("game_date") < test_start)
        test_df = df.filter(
            (pl.col("game_date") >= test_start) & (pl.col("game_date") <= test_end)
        )
        if not train_df.height or not test_df.height:
            continue

        X_train, y_train = to_matrix(train_df), train_df["got_hit"].to_numpy()
        X_test, y_test = to_matrix(test_df), test_df["got_hit"].to_numpy()

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
            results[name].append(FoldResult(
                name, test_start, test_end, train_df.height, test_df.height, metrics,
            ))

    return results


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
    args = parser.parse_args()

    df = load_dataset(Path(args.dataset))
    results = run_walk_forward(df, DEFAULT_FOLDS)
    coefficients = logistic_coefficients(df)
    report = print_report(df, results, coefficients)

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
