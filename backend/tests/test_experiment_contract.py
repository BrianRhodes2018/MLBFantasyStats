import copy

import pytest

from hit_model.experiment_contract import (
    baseline_folds,
    contract_fingerprint,
    load_experiment_contract,
    validate_experiment_contract,
)


def test_committed_v3_contract_is_locked_and_valid():
    contract = load_experiment_contract()
    assert contract["locked"] is True
    assert contract["metrics"]["primary"] == "top10_hit_rate"
    assert len(baseline_folds(contract)) == 6
    assert len(contract_fingerprint(contract)) == 64


def test_contract_rejects_final_test_overlap():
    contract = copy.deepcopy(load_experiment_contract())
    contract["evaluation"]["locked_final_backtest"]["test_start"] = "2026-05-31"
    with pytest.raises(ValueError, match="after development"):
        validate_experiment_contract(contract)


def test_contract_rejects_unlocked_or_row_level_bootstrap():
    contract = copy.deepcopy(load_experiment_contract())
    contract["locked"] = False
    with pytest.raises(ValueError, match="locked"):
        validate_experiment_contract(contract)

    contract = copy.deepcopy(load_experiment_contract())
    contract["evaluation"]["bootstrap"]["cluster"] = "candidate"
    with pytest.raises(ValueError, match="game_date"):
        validate_experiment_contract(contract)
