"""Three daily strikeout projection approaches with chronological backtests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from hashlib import sha256
from math import sqrt
from typing import Any, Iterable, Mapping, Optional

import numpy as np
from scipy.stats import betabinom, norm
from sklearn.ensemble import HistGradientBoostingRegressor

from .features import FEATURE_NAMES


APPROACH_ORDER = ("decomposed", "count", "bayes")
MODEL_VERSION = "pitcher_ks_v1"
MAX_K_SUPPORT = 20


@dataclass(frozen=True)
class PitcherKProjection:
    projected_ks: float
    median_ks: int
    p10_ks: int
    p90_ks: int
    probability_5_plus: float
    probability_6_plus: float
    projected_batters_faced: float
    pmf: list[float]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def feature_matrix(rows: Iterable[Mapping[str, Any]]) -> np.ndarray:
    values = [
        [float(row.get(name, np.nan)) for name in FEATURE_NAMES]
        for row in rows
    ]
    return np.asarray(values, dtype=float)


def _normalize_pmf(values: np.ndarray) -> np.ndarray:
    clean = np.nan_to_num(values.astype(float), nan=0.0, posinf=0.0, neginf=0.0)
    clean = np.clip(clean, 0.0, None)
    total = float(clean.sum())
    if total <= 0:
        clean = np.zeros(MAX_K_SUPPORT + 1, dtype=float)
        clean[0] = 1.0
        return clean
    return clean / total


def _projection_from_pmf(pmf: np.ndarray, projected_bf: float) -> PitcherKProjection:
    pmf = _normalize_pmf(pmf)
    support = np.arange(len(pmf))
    cdf = np.cumsum(pmf)

    def quantile(probability: float) -> int:
        return int(np.searchsorted(cdf, probability, side="left"))

    return PitcherKProjection(
        projected_ks=round(float(np.dot(support, pmf)), 2),
        median_ks=quantile(0.5),
        p10_ks=quantile(0.1),
        p90_ks=quantile(0.9),
        probability_5_plus=round(float(pmf[5:].sum()), 6),
        probability_6_plus=round(float(pmf[6:].sum()), 6),
        projected_batters_faced=round(float(projected_bf), 2),
        pmf=[round(float(value), 8) for value in pmf],
    )


def _beta_binomial_mixture(
    *,
    k_rate: float,
    projected_bf: float,
    bf_sigma: float,
    concentration: float,
) -> np.ndarray:
    k_rate = float(np.clip(k_rate, 0.06, 0.48))
    projected_bf = float(np.clip(projected_bf, 10.0, 36.0))
    bf_sigma = float(np.clip(bf_sigma, 1.25, 6.0))
    bf_values = np.arange(10, 38)
    weights = norm.pdf(bf_values, loc=projected_bf, scale=bf_sigma)
    weights = weights / weights.sum()
    alpha = max(k_rate * concentration, 0.1)
    beta = max((1.0 - k_rate) * concentration, 0.1)
    pmf = np.zeros(MAX_K_SUPPORT + 1, dtype=float)
    for batters_faced, weight in zip(bf_values, weights):
        upper = min(int(batters_faced), MAX_K_SUPPORT)
        counts = np.arange(upper + 1)
        partial = betabinom.pmf(counts, int(batters_faced), alpha, beta)
        pmf[: upper + 1] += weight * partial
        if batters_faced > MAX_K_SUPPORT:
            pmf[-1] += weight * max(0.0, 1.0 - float(partial.sum()))
    return _normalize_pmf(pmf)


def _discretized_normal_pmf(mean: float, sigma: float) -> np.ndarray:
    mean = float(np.clip(mean, 0.0, 16.0))
    sigma = float(np.clip(sigma, 1.0, 6.0))
    pmf = np.zeros(MAX_K_SUPPORT + 1, dtype=float)
    pmf[0] = norm.cdf(0.5, loc=mean, scale=sigma)
    for count in range(1, MAX_K_SUPPORT):
        pmf[count] = norm.cdf(count + 0.5, loc=mean, scale=sigma) - norm.cdf(
            count - 0.5,
            loc=mean,
            scale=sigma,
        )
    pmf[-1] = 1.0 - norm.cdf(MAX_K_SUPPORT - 0.5, loc=mean, scale=sigma)
    return _normalize_pmf(pmf)


class EmpiricalBayesApproach:
    name = "bayes"

    def fit(self, rows: list[Mapping[str, Any]]) -> "EmpiricalBayesApproach":
        self.training_rows = len(rows)
        if rows:
            self.global_bf_sigma = max(
                2.0,
                float(np.std([float(row["batters_faced"]) for row in rows])),
            )
        else:
            self.global_bf_sigma = 3.5
        return self

    def predict(self, rows: list[Mapping[str, Any]]) -> list[PitcherKProjection]:
        output: list[PitcherKProjection] = []
        for row in rows:
            pitcher_rate = float(row["pitcher_k_rate"])
            recent_rate = float(row["pitcher_recent_k_rate"])
            lineup_rate = float(row["opponent_lineup_k_rate"])
            team_rate = float(row["opponent_team_k_rate"])
            league_rate = float(row["league_k_rate"])
            k_rate = (
                0.48 * pitcher_rate
                + 0.16 * recent_rate
                + 0.22 * lineup_rate
                + 0.09 * team_rate
                + 0.05 * league_rate
            )
            projected_bf = (
                0.62 * float(row["pitcher_bf_avg"])
                + 0.30 * float(row["pitcher_recent_bf_avg"])
                + 0.08 * 22.5
            )
            pmf = _beta_binomial_mixture(
                k_rate=k_rate,
                projected_bf=projected_bf,
                bf_sigma=self.global_bf_sigma,
                concentration=48.0,
            )
            output.append(_projection_from_pmf(pmf, projected_bf))
        return output


class DecomposedSimulationApproach:
    name = "decomposed"

    def __init__(self) -> None:
        self.k_rate_model = HistGradientBoostingRegressor(
            loss="squared_error",
            learning_rate=0.045,
            max_iter=220,
            max_leaf_nodes=15,
            min_samples_leaf=28,
            l2_regularization=1.0,
            random_state=41,
        )
        self.workload_model = HistGradientBoostingRegressor(
            loss="squared_error",
            learning_rate=0.045,
            max_iter=200,
            max_leaf_nodes=15,
            min_samples_leaf=28,
            l2_regularization=1.0,
            random_state=43,
        )
        self.bf_sigma = 3.5

    def fit(self, rows: list[Mapping[str, Any]]) -> "DecomposedSimulationApproach":
        matrix = feature_matrix(rows)
        batters_faced = np.asarray([max(float(row["batters_faced"]), 1.0) for row in rows])
        strikeouts = np.asarray([float(row["strikeouts"]) for row in rows])
        k_rates = strikeouts / batters_faced
        self.k_rate_model.fit(matrix, k_rates)
        self.workload_model.fit(matrix, batters_faced)
        workload_predictions = self.workload_model.predict(matrix)
        self.bf_sigma = max(1.75, float(np.std(batters_faced - workload_predictions)))
        self.training_rows = len(rows)
        return self

    def predict(self, rows: list[Mapping[str, Any]]) -> list[PitcherKProjection]:
        matrix = feature_matrix(rows)
        k_rates = np.clip(self.k_rate_model.predict(matrix), 0.06, 0.48)
        workloads = np.clip(self.workload_model.predict(matrix), 10.0, 36.0)
        output: list[PitcherKProjection] = []
        for k_rate, projected_bf in zip(k_rates, workloads):
            pmf = _beta_binomial_mixture(
                k_rate=float(k_rate),
                projected_bf=float(projected_bf),
                bf_sigma=self.bf_sigma,
                concentration=75.0,
            )
            output.append(_projection_from_pmf(pmf, float(projected_bf)))
        return output


class DirectCountQuantileApproach:
    name = "count"

    def __init__(self) -> None:
        common = {
            "learning_rate": 0.045,
            "max_iter": 240,
            "max_leaf_nodes": 15,
            "min_samples_leaf": 28,
            "l2_regularization": 1.25,
        }
        self.mean_model = HistGradientBoostingRegressor(
            loss="poisson",
            random_state=47,
            **common,
        )
        self.lower_model = HistGradientBoostingRegressor(
            loss="quantile",
            quantile=0.1,
            random_state=53,
            **common,
        )
        self.upper_model = HistGradientBoostingRegressor(
            loss="quantile",
            quantile=0.9,
            random_state=59,
            **common,
        )
        self.residual_sigma = 2.2

    def fit(self, rows: list[Mapping[str, Any]]) -> "DirectCountQuantileApproach":
        matrix = feature_matrix(rows)
        strikeouts = np.asarray([float(row["strikeouts"]) for row in rows])
        self.mean_model.fit(matrix, strikeouts)
        self.lower_model.fit(matrix, strikeouts)
        self.upper_model.fit(matrix, strikeouts)
        residuals = strikeouts - self.mean_model.predict(matrix)
        self.residual_sigma = max(1.25, float(np.std(residuals)))
        self.training_rows = len(rows)
        return self

    def predict(self, rows: list[Mapping[str, Any]]) -> list[PitcherKProjection]:
        matrix = feature_matrix(rows)
        means = np.clip(self.mean_model.predict(matrix), 0.0, 16.0)
        lowers = np.clip(self.lower_model.predict(matrix), 0.0, 16.0)
        uppers = np.clip(self.upper_model.predict(matrix), 0.0, 20.0)
        output: list[PitcherKProjection] = []
        for row, mean, lower, upper in zip(rows, means, lowers, uppers):
            lo = min(float(lower), float(upper))
            hi = max(float(lower), float(upper))
            quantile_sigma = (hi - lo) / (2.0 * 1.281551565545)
            sigma = max(1.0, quantile_sigma, self.residual_sigma * 0.55)
            pmf = _discretized_normal_pmf(float(mean), sigma)
            projected_bf = (
                0.65 * float(row["pitcher_bf_avg"])
                + 0.35 * float(row["pitcher_recent_bf_avg"])
            )
            output.append(_projection_from_pmf(pmf, projected_bf))
        return output


def make_approaches() -> dict[str, Any]:
    return {
        "decomposed": DecomposedSimulationApproach(),
        "count": DirectCountQuantileApproach(),
        "bayes": EmpiricalBayesApproach(),
    }


def _metrics(
    rows: list[Mapping[str, Any]],
    predictions: list[PitcherKProjection],
) -> dict[str, float | int]:
    actual = np.asarray([float(row["strikeouts"]) for row in rows])
    predicted = np.asarray([projection.projected_ks for projection in predictions])
    errors = predicted - actual
    log_scores: list[float] = []
    interval_hits = 0
    for observed, projection in zip(actual.astype(int), predictions):
        probability = (
            projection.pmf[observed]
            if 0 <= observed < len(projection.pmf)
            else 1e-9
        )
        log_scores.append(-float(np.log(max(probability, 1e-9))))
        interval_hits += int(projection.p10_ks <= observed <= projection.p90_ks)
    return {
        "starts": len(rows),
        "mae": round(float(np.mean(np.abs(errors))), 4),
        "rmse": round(float(sqrt(np.mean(np.square(errors)))), 4),
        "bias": round(float(np.mean(errors)), 4),
        "mean_log_score": round(float(np.mean(log_scores)), 4),
        "interval_80_coverage": round(interval_hits / len(rows), 4),
    }


def _chronological_folds(rows: list[Mapping[str, Any]]) -> list[tuple[str, list[int], list[int]]]:
    folds: list[tuple[str, list[int], list[int]]] = []
    dates = [date.fromisoformat(str(row["game_date"])) for row in rows]
    for test_year in (2025, 2026):
        train_indices = [index for index, value in enumerate(dates) if value.year < test_year]
        test_indices = [index for index, value in enumerate(dates) if value.year == test_year]
        if len(train_indices) >= 500 and len(test_indices) >= 100:
            folds.append((str(test_year), train_indices, test_indices))
    if folds:
        return folds
    unique_dates = sorted(set(dates))
    if len(unique_dates) < 5:
        return []
    cutoff = unique_dates[max(1, int(len(unique_dates) * 0.8) - 1)]
    train_indices = [index for index, value in enumerate(dates) if value <= cutoff]
    test_indices = [index for index, value in enumerate(dates) if value > cutoff]
    if train_indices and test_indices:
        folds.append(("last_20_percent", train_indices, test_indices))
    return folds


def chronological_backtest(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    fold_results: list[dict[str, Any]] = []
    combined: dict[str, list[dict[str, float | int]]] = {
        approach: [] for approach in APPROACH_ORDER
    }
    for fold_name, train_indices, test_indices in _chronological_folds(rows):
        train_rows = [rows[index] for index in train_indices]
        test_rows = [rows[index] for index in test_indices]
        approach_results: dict[str, Any] = {}
        for approach_name, model in make_approaches().items():
            model.fit(train_rows)
            fold_metrics = _metrics(test_rows, model.predict(test_rows))
            approach_results[approach_name] = fold_metrics
            combined[approach_name].append(fold_metrics)
        fold_results.append({
            "fold": fold_name,
            "train_starts": len(train_rows),
            "test_starts": len(test_rows),
            "test_start": min(str(row["game_date"]) for row in test_rows),
            "test_end": max(str(row["game_date"]) for row in test_rows),
            "approaches": approach_results,
        })

    aggregate: dict[str, Any] = {}
    for approach, results in combined.items():
        if not results:
            continue
        total = sum(int(result["starts"]) for result in results)
        aggregate[approach] = {
            "starts": total,
            **{
                key: round(
                    sum(float(result[key]) * int(result["starts"]) for result in results) / total,
                    4,
                )
                for key in (
                    "mae",
                    "rmse",
                    "bias",
                    "mean_log_score",
                    "interval_80_coverage",
                )
            },
        }
    return {"folds": fold_results, "aggregate": aggregate}


def dataset_profile(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    dates = [str(row["game_date"]) for row in rows]
    actual_ks = np.asarray([float(row["strikeouts"]) for row in rows])
    actual_bf = np.asarray([float(row["batters_faced"]) for row in rows])
    missing_by_feature = {
        feature: round(
            sum(
                row.get(feature) is None
                or not np.isfinite(float(row.get(feature, np.nan)))
                for row in rows
            ) / len(rows),
            6,
        )
        for feature in FEATURE_NAMES
    }
    identities = sorted(
        f"{row['game_date']}|{row['game_pk']}|{row['pitcher_id']}" for row in rows
    )
    return {
        "grain": "one row per actual MLB starting pitcher and game",
        "starts": len(rows),
        "unique_pitchers": len({int(row["pitcher_id"]) for row in rows}),
        "first_date": min(dates),
        "last_date": max(dates),
        "duplicate_identity_rows": len(rows) - len(set(identities)),
        "mean_strikeouts": round(float(actual_ks.mean()), 4),
        "strikeout_p95": round(float(np.quantile(actual_ks, 0.95)), 2),
        "mean_batters_faced": round(float(actual_bf.mean()), 4),
        "invalid_target_rows": int(
            np.sum((actual_ks < 0) | (actual_bf <= 0) | (actual_ks > actual_bf))
        ),
        "missing_rate_by_feature": missing_by_feature,
        "identity_sha256": sha256("\n".join(identities).encode("utf-8")).hexdigest(),
    }


def train_model_package(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    if len(rows) < 500:
        raise ValueError("Pitcher Ks training requires at least 500 historical starts.")
    profile = dataset_profile(rows)
    if profile["duplicate_identity_rows"] or profile["invalid_target_rows"]:
        raise ValueError(f"Pitcher Ks dataset failed quality checks: {profile}")
    backtest = chronological_backtest(rows)
    approaches = make_approaches()
    for model in approaches.values():
        model.fit(rows)
    trained_through = max(str(row["game_date"]) for row in rows)
    return {
        "model_version": MODEL_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "trained_through": trained_through,
        "trained_on_rows": len(rows),
        "data_profile": profile,
        "backtest": backtest,
        "approaches": approaches,
    }


def score_approaches(
    package: Mapping[str, Any],
    candidates: list[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    if list(package.get("feature_names") or []) != FEATURE_NAMES:
        raise ValueError("Pitcher Ks artifact feature schema does not match runtime code.")
    output: dict[str, list[dict[str, Any]]] = {}
    for approach in APPROACH_ORDER:
        model = package["approaches"][approach]
        predictions = model.predict(candidates)
        output[approach] = [
            {**dict(candidate), **prediction.as_dict()}
            for candidate, prediction in zip(candidates, predictions)
        ]
    return output
