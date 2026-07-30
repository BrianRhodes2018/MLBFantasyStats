"""Model contracts and estimators for the Hit Picks V3 experiment ladder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import polars as pl
from sklearn.ensemble import HistGradientBoostingClassifier

from hit_model.opportunity import (
    PA_BUCKETS,
    marginal_hit_probability_constant_rate,
    plate_appearance_bucket,
)
from ml_environment import json_fingerprint
from train_hit_model import FEATURES as V2_FEATURES
from train_hit_model import GBM_RECIPE, prepare_frame


OPPORTUNITY_FEATURES = [
    "lineup_confirmed",
    "lineup_confidence",
    "lineup_confidence_missing",
    "top_order_indicator",
    "team_recent_pa_per_game",
    "team_recent_runs_per_game",
    "team_recent_hits_per_game",
    "team_recent_games",
]

BATTER_CONTACT_FEATURES = [
    "batter_pitch_sample",
    "batter_swing_sample",
    "batter_bip_sample",
    "batter_contact_rate",
    "batter_whiff_rate",
    "batter_bip_hit_rate",
    "batter_avg_exit_velocity",
    "batter_hard_hit_rate",
    "batter_contact_missing",
    "batter_hand_pitch_sample",
    "batter_hand_swing_sample",
    "batter_hand_bip_sample",
    "batter_hand_contact_rate",
    "batter_hand_whiff_rate",
    "batter_hand_bip_hit_rate",
    "batter_hand_avg_exit_velocity",
    "batter_hand_hard_hit_rate",
    "batter_hand_contact_missing",
]

ARSENAL_FEATURES = [
    "pitcher_pitch_sample",
    "pitcher_swing_sample",
    "pitcher_bip_sample",
    "pitcher_contact_allowed",
    "pitcher_whiff_rate",
    "pitcher_bip_hit_rate",
    "pitcher_avg_exit_velocity_allowed",
    "pitcher_hard_hit_rate_allowed",
    "pitcher_avg_velocity",
    "pitcher_avg_horizontal_break",
    "pitcher_avg_induced_vertical_break",
    "pitcher_avg_extension",
    "pitcher_pitch_data_missing",
    "arsenal_match_contact",
    "arsenal_match_whiff",
    "arsenal_match_bip_hit",
    "arsenal_match_exit_velocity",
    "arsenal_coverage",
    "arsenal_pitch_families",
    "arsenal_usage_entropy",
    "arsenal_matchup_missing",
]

WORKLOAD_BULLPEN_FEATURES = [
    "starter_workload_sample",
    "starter_last3_pitches",
    "starter_last3_batters_faced",
    "starter_last3_innings",
    "starter_days_rest",
    "starter_short_start_rate",
    "bullpen_pitches_yesterday",
    "bullpen_pitches_last3_days",
    "bullpen_relievers_yesterday",
    "bullpen_relievers_last3_days",
    "bullpen_recent_appearances",
]


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


FEATURE_LADDER = {
    "E1": list(V2_FEATURES),
    "E2": _unique([*V2_FEATURES, *OPPORTUNITY_FEATURES]),
    "E3": _unique(
        [*V2_FEATURES, *OPPORTUNITY_FEATURES, *BATTER_CONTACT_FEATURES]
    ),
    "E4": _unique(
        [
            *V2_FEATURES,
            *OPPORTUNITY_FEATURES,
            *BATTER_CONTACT_FEATURES,
            *ARSENAL_FEATURES,
        ]
    ),
    "E5": _unique(
        [
            *V2_FEATURES,
            *OPPORTUNITY_FEATURES,
            *BATTER_CONTACT_FEATURES,
            *ARSENAL_FEATURES,
            *WORKLOAD_BULLPEN_FEATURES,
        ]
    ),
}

V3_GBM_RECIPE = {
    **GBM_RECIPE,
    "max_iter": 450,
    "model_version": "hit_gbm_v3",
}
OPPORTUNITY_RECIPE = {
    "estimator": "sklearn.ensemble.HistGradientBoostingClassifier",
    "loss": "log_loss",
    "max_depth": 3,
    "learning_rate": 0.06,
    "max_iter": 350,
    "min_samples_leaf": 80,
    "l2_regularization": 1.0,
    "early_stopping": True,
    "validation_fraction": 0.15,
    "random_state": 7,
}
PER_PA_RECIPE = {
    **OPPORTUNITY_RECIPE,
    "max_iter": 400,
    "min_samples_leaf": 100,
}

PA_CLASS_BY_BUCKET = {bucket: index for index, bucket in enumerate(PA_BUCKETS)}


def feature_set(experiment_id: str) -> list[str]:
    try:
        return FEATURE_LADDER[experiment_id]
    except KeyError as exc:
        raise ValueError(
            f"Unknown V3 experiment {experiment_id}; "
            f"expected one of {sorted(FEATURE_LADDER)}."
        ) from exc


def v3_recipe_fingerprint(
    experiment_id: str,
    *,
    architecture: str = "game_level",
) -> str:
    return json_fingerprint(
        {
            "architecture": architecture,
            "experiment_id": experiment_id,
            "features": feature_set(experiment_id),
            "game_recipe": V3_GBM_RECIPE,
            "opportunity_recipe": OPPORTUNITY_RECIPE,
            "per_pa_recipe": PER_PA_RECIPE,
        }
    )


def prepare_v3_frame(df: pl.DataFrame) -> pl.DataFrame:
    """Apply shared V2 transforms and validate the V3 label columns."""
    prepared = prepare_frame(df)
    if "pa_game" in prepared.columns:
        prepared = prepared.with_columns(
            pl.col("pa_game")
            .fill_null(0)
            .cast(pl.Int16)
            .map_elements(
                lambda value: PA_CLASS_BY_BUCKET[
                    plate_appearance_bucket(int(value))
                ],
                return_dtype=pl.Int8,
            )
            .alias("pa_class")
        )
    return prepared


def validate_feature_columns(
    df: pl.DataFrame,
    features: Sequence[str],
) -> None:
    missing = [feature for feature in features if feature not in df.columns]
    if missing:
        raise ValueError(
            "V3 dataset is missing required feature columns: "
            + ", ".join(missing)
        )


def to_v3_matrix(
    df: pl.DataFrame,
    features: Sequence[str],
) -> np.ndarray:
    validate_feature_columns(df, features)
    return df.select(list(features)).to_numpy().astype(np.float64)


def make_v3_game_model() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        **{
            key: value
            for key, value in V3_GBM_RECIPE.items()
            if key not in {"estimator", "model_version"}
        }
    )


def opportunity_feature_set() -> list[str]:
    # Batting slot, home/away, lineup confidence, team opportunity, and
    # starter workload are known before first pitch and directly determine PA.
    return _unique(
        [
            "batting_order",
            "is_home",
            *OPPORTUNITY_FEATURES,
            "p_season_starts",
            "p_last3_ip",
            "starter_workload_sample",
            "starter_last3_batters_faced",
            "starter_last3_innings",
            "starter_days_rest",
        ]
    )


def per_pa_feature_set(experiment_id: str = "E5") -> list[str]:
    # Opportunity-only variables stay out of the per-PA contact probability.
    excluded = set(OPPORTUNITY_FEATURES)
    return [
        feature
        for feature in feature_set(experiment_id)
        if feature not in excluded
    ]


def fit_game_model(
    df: pl.DataFrame,
    *,
    experiment_id: str,
) -> HistGradientBoostingClassifier:
    features = feature_set(experiment_id)
    model = make_v3_game_model()
    model.fit(
        to_v3_matrix(df, features),
        df["got_hit"].cast(pl.Int8).to_numpy(),
    )
    return model


def make_opportunity_model() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        **{
            key: value
            for key, value in OPPORTUNITY_RECIPE.items()
            if key != "estimator"
        }
    )


def fit_opportunity_model(
    df: pl.DataFrame,
) -> HistGradientBoostingClassifier:
    features = opportunity_feature_set()
    model = make_opportunity_model()
    model.fit(
        to_v3_matrix(df, features),
        df["pa_class"].to_numpy(),
    )
    return model


def pa_distributions(
    model: HistGradientBoostingClassifier,
    df: pl.DataFrame,
) -> np.ndarray:
    raw = model.predict_proba(to_v3_matrix(df, opportunity_feature_set()))
    distributions = np.zeros((df.height, len(PA_BUCKETS)), dtype=np.float64)
    for source_index, class_value in enumerate(model.classes_):
        distributions[:, int(class_value)] = raw[:, source_index]
    totals = distributions.sum(axis=1)
    if np.any(totals <= 0):
        raise ValueError("Opportunity model produced an empty PA distribution.")
    return distributions / totals[:, None]


def make_per_pa_model() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        **{
            key: value
            for key, value in PER_PA_RECIPE.items()
            if key != "estimator"
        }
    )


def fit_per_pa_model(
    df: pl.DataFrame,
    *,
    experiment_id: str = "E5",
) -> HistGradientBoostingClassifier:
    """Fit a per-PA hit model without materializing one row per appearance.

    Each batter-game contributes a positive copy weighted by hits and a
    negative copy weighted by non-hit plate appearances.
    """
    eligible = df.filter(pl.col("pa_game") > 0)
    features = per_pa_feature_set(experiment_id)
    matrix = to_v3_matrix(eligible, features)
    hits = eligible["hits_game"].fill_null(0).to_numpy().astype(np.float64)
    plate_appearances = (
        eligible["pa_game"].fill_null(0).to_numpy().astype(np.float64)
    )
    misses = np.maximum(plate_appearances - hits, 0.0)
    X = np.concatenate([matrix, matrix], axis=0)
    y = np.concatenate(
        [np.ones(eligible.height), np.zeros(eligible.height)]
    )
    weights = np.concatenate([hits, misses])
    nonzero = weights > 0
    model = make_per_pa_model()
    model.fit(X[nonzero], y[nonzero], sample_weight=weights[nonzero])
    return model


def decomposed_probabilities(
    *,
    opportunity_model: HistGradientBoostingClassifier,
    per_pa_model: HistGradientBoostingClassifier,
    df: pl.DataFrame,
    experiment_id: str = "E5",
    six_plus_representative_pa: float = 6.25,
) -> np.ndarray:
    distributions = pa_distributions(opportunity_model, df)
    per_pa = per_pa_model.predict_proba(
        to_v3_matrix(df, per_pa_feature_set(experiment_id))
    )[:, 1]
    output = np.empty(df.height, dtype=np.float64)
    for index in range(df.height):
        distribution = {
            bucket: float(distributions[index, bucket_index])
            for bucket_index, bucket in enumerate(PA_BUCKETS)
        }
        output[index] = marginal_hit_probability_constant_rate(
            distribution,
            float(per_pa[index]),
            six_plus_representative_pa=six_plus_representative_pa,
        )
    return output


@dataclass(frozen=True)
class V3BundleMetadata:
    model_version: str
    experiment_id: str
    architecture: str
    trained_on_rows: int
    training_end: str
    feature_schema_sha256: str
    recipe_sha256: str


def bundle_metadata(
    df: pl.DataFrame,
    *,
    experiment_id: str,
    architecture: str,
) -> V3BundleMetadata:
    features = (
        feature_set(experiment_id)
        if architecture == "game_level"
        else _unique(
            [*opportunity_feature_set(), *per_pa_feature_set(experiment_id)]
        )
    )
    return V3BundleMetadata(
        model_version="hit_gbm_v3",
        experiment_id=experiment_id,
        architecture=architecture,
        trained_on_rows=df.height,
        training_end=str(df["game_date"].max()),
        feature_schema_sha256=json_fingerprint(features),
        recipe_sha256=v3_recipe_fingerprint(
            experiment_id,
            architecture=architecture,
        ),
    )


def feature_coverage(
    df: pl.DataFrame,
    features: Sequence[str],
) -> dict[str, Any]:
    validate_feature_columns(df, features)
    rows = max(df.height, 1)
    missing = {
        feature: round(df[feature].null_count() / rows, 6)
        for feature in features
    }
    return {
        "rows": df.height,
        "features": len(features),
        "mean_missing_fraction": round(
            sum(missing.values()) / max(len(missing), 1),
            6,
        ),
        "missing_fraction": missing,
    }


def coverage_fallback_counts(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    records = list(rows)
    return {
        "candidate_count": len(records),
        "missing_batter_contact": sum(
            int(record.get("batter_contact_missing") or 0)
            for record in records
        ),
        "missing_pitcher_pitch_data": sum(
            int(record.get("pitcher_pitch_data_missing") or 0)
            for record in records
        ),
        "missing_arsenal_matchup": sum(
            int(record.get("arsenal_matchup_missing") or 0)
            for record in records
        ),
        "projected_lineups": sum(
            1
            for record in records
            if not int(record.get("lineup_confirmed") or 0)
        ),
    }

