"""V3 feature growth must not break V2's historical training load."""

from __future__ import annotations

import polars as pl

from predict_hits_today import combine_v2_training_frames
from train_hit_model import FEATURES


def _frame(*, extra: bool) -> pl.DataFrame:
    values = {feature: [0.0] for feature in FEATURES}
    values.update({
        "got_hit": [0],
        "pa_game": [4],
        "is_home": [1],
        "platoon_advantage": [0],
    })
    if extra:
        values["arsenal_match_contact"] = [0.72]
    return pl.DataFrame(values)


def test_augmented_current_rows_coexist_with_legacy_v2_rows():
    combined = combine_v2_training_frames([
        ("current", _frame(extra=True)),
        ("historical", _frame(extra=False)),
    ])
    assert combined.height == 2
    assert "arsenal_match_contact" in combined.columns
    assert combined["arsenal_match_contact"].null_count() == 1
