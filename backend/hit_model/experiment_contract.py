"""Frozen experiment contract for V3 model development.

The contract is intentionally data rather than Python constants so a reviewer
can see the exact folds, holdout, metrics, and promotion gates without reading
the experiment runner. Validation fails closed before any model is trained.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT_PATH = BACKEND_DIR / "config" / "hit_model_v3_experiment.json"


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO date, got {value!r}.") from exc


def _window(window: dict[str, Any], field: str) -> tuple[date, date]:
    start = _parse_date(window.get("test_start"), f"{field}.test_start")
    end = _parse_date(window.get("test_end"), f"{field}.test_end")
    if end < start:
        raise ValueError(f"{field} ends before it starts.")
    return start, end


def canonical_contract_bytes(contract: dict[str, Any]) -> bytes:
    return json.dumps(
        contract,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def contract_fingerprint(contract: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_contract_bytes(contract)).hexdigest()


def validate_experiment_contract(contract: dict[str, Any]) -> dict[str, Any]:
    if contract.get("contract_version") != "hit_model_v3_experiment_v1":
        raise ValueError("Unsupported or missing V3 experiment contract version.")
    if contract.get("locked") is not True:
        raise ValueError("V3 experiment contract must be locked before results are run.")
    if contract.get("status") != "frozen_before_v3_results":
        raise ValueError("V3 contract status must be frozen_before_v3_results.")

    evaluation = contract.get("evaluation") or {}
    folds = evaluation.get("development_folds") or []
    if len(folds) < 3:
        raise ValueError("At least three chronological development folds are required.")

    seen_names: set[str] = set()
    prior_end: date | None = None
    for index, fold in enumerate(folds):
        name = str(fold.get("name") or "")
        if not name or name in seen_names:
            raise ValueError("Development fold names must be present and unique.")
        seen_names.add(name)
        start, end = _window(fold, f"evaluation.development_folds[{index}]")
        if prior_end is not None and start <= prior_end:
            raise ValueError("Development folds must be ordered and non-overlapping.")
        prior_end = end

    final = evaluation.get("locked_final_backtest") or {}
    final_start, final_end = _window(final, "evaluation.locked_final_backtest")
    if prior_end is not None and final_start <= prior_end:
        raise ValueError("Locked final backtest must begin after development folds.")
    if final.get("v3_results_must_remain_hidden_until_candidate_is_frozen") is not True:
        raise ValueError("Locked final results must remain hidden until candidate freeze.")

    calibration = evaluation.get("calibration_fit_oos_window") or {}
    calibration_start = _parse_date(
        calibration.get("start"),
        "evaluation.calibration_fit_oos_window.start",
    )
    calibration_end = _parse_date(
        calibration.get("end"),
        "evaluation.calibration_fit_oos_window.end",
    )
    if calibration_end < calibration_start or calibration_end >= final_start:
        raise ValueError("Calibration-fit OOS data must end before the final backtest.")

    shadow = evaluation.get("live_shadow") or {}
    shadow_start = _parse_date(shadow.get("start"), "evaluation.live_shadow.start")
    if shadow_start <= final_end:
        raise ValueError("Live shadow must start after the locked final backtest.")
    if int(shadow.get("minimum_completed_game_dates") or 0) < 20:
        raise ValueError("Live shadow requires at least 20 completed game dates.")
    if int(shadow.get("minimum_top10_picks") or 0) < 200:
        raise ValueError("Live shadow requires at least 200 top-10 picks.")

    bootstrap = evaluation.get("bootstrap") or {}
    if bootstrap.get("cluster") != "game_date":
        raise ValueError("Uncertainty must be clustered by game_date.")
    if not 0.8 <= float(bootstrap.get("confidence_level") or 0) < 1:
        raise ValueError("Bootstrap confidence level is invalid.")
    if int(bootstrap.get("iterations") or 0) < 1000:
        raise ValueError("At least 1,000 bootstrap iterations are required.")

    metrics = contract.get("metrics") or {}
    if metrics.get("primary") != "top10_hit_rate":
        raise ValueError("The predeclared primary metric must be top10_hit_rate.")

    promotion = contract.get("promotion_gates") or {}
    for gate_name in ("shadow_entry", "primary_promotion"):
        if not promotion.get(gate_name):
            raise ValueError(f"Missing promotion gate: {gate_name}.")

    modeling = contract.get("modeling") or {}
    if modeling.get("candidate_population_must_match_baseline") is not True:
        raise ValueError("Candidate-population parity must be mandatory.")
    if modeling.get("missing_optional_features_must_use_explicit_fallbacks") is not True:
        raise ValueError("Missing-feature fallbacks must be mandatory.")
    return contract


def load_experiment_contract(
    path: Path | str = DEFAULT_CONTRACT_PATH,
) -> dict[str, Any]:
    contract_path = Path(path)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    return validate_experiment_contract(contract)


def development_folds(contract: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (fold["test_start"], fold["test_end"])
        for fold in contract["evaluation"]["development_folds"]
    ]


def baseline_folds(contract: dict[str, Any]) -> list[tuple[str, str]]:
    folds = development_folds(contract)
    final = contract["evaluation"]["locked_final_backtest"]
    return [*folds, (final["test_start"], final["test_end"])]
