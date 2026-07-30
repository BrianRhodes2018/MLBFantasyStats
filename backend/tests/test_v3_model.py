import numpy as np
import polars as pl
import pytest

from hit_model.opportunity import PA_BUCKETS
from hit_model.v3_model import (
    FEATURE_LADDER,
    OPPORTUNITY_FEATURES,
    decomposed_probabilities,
    feature_set,
    pa_distributions,
    prepare_v3_frame,
    v3_recipe_fingerprint,
)


def test_feature_ladder_is_strictly_additive():
    assert set(FEATURE_LADDER["E1"]) < set(FEATURE_LADDER["E2"])
    assert set(FEATURE_LADDER["E2"]) < set(FEATURE_LADDER["E3"])
    assert set(FEATURE_LADDER["E3"]) < set(FEATURE_LADDER["E4"])
    assert set(FEATURE_LADDER["E4"]) < set(FEATURE_LADDER["E5"])
    assert set(OPPORTUNITY_FEATURES).issubset(feature_set("E2"))


def test_recipe_fingerprint_changes_with_architecture():
    assert v3_recipe_fingerprint("E5") != v3_recipe_fingerprint(
        "E5",
        architecture="decomposed",
    )


def test_prepare_v3_frame_covers_zero_through_six_plus():
    frame = pl.DataFrame({
        "pa_game": [0, 1, 2, 3, 4, 5, 6, 8],
        "season_k_pct": [20.0] * 8,
        "p_season_k_pct": [22.0] * 8,
        "is_home": [True] * 8,
        "platoon_advantage": [1] * 8,
        "got_hit": [False] * 8,
    })
    prepared = prepare_v3_frame(frame)
    assert prepared["pa_class"].to_list() == [0, 1, 2, 3, 4, 5, 6, 6]


class StubOpportunity:
    classes_ = np.arange(len(PA_BUCKETS))

    def predict_proba(self, matrix):
        probabilities = np.zeros((matrix.shape[0], len(PA_BUCKETS)))
        probabilities[:, 4] = 1.0
        return probabilities


class StubPerPa:
    def predict_proba(self, matrix):
        return np.column_stack([
            np.full(matrix.shape[0], 0.75),
            np.full(matrix.shape[0], 0.25),
        ])


def test_pa_distribution_and_decomposed_math(monkeypatch):
    import hit_model.v3_model as module

    monkeypatch.setattr(module, "opportunity_feature_set", lambda: ["x"])
    monkeypatch.setattr(
        module,
        "per_pa_feature_set",
        lambda experiment_id="E5": ["x"],
    )
    frame = pl.DataFrame({"x": [1.0, 2.0]})
    distributions = pa_distributions(StubOpportunity(), frame)
    assert distributions[:, 4].tolist() == [1.0, 1.0]

    probabilities = decomposed_probabilities(
        opportunity_model=StubOpportunity(),
        per_pa_model=StubPerPa(),
        df=frame,
    )
    assert probabilities.tolist() == pytest.approx(
        [1 - 0.75**4, 1 - 0.75**4]
    )

